"""Deterministic citation registry and citation-safety helpers."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from factori.artifacts import ArtifactStore
from factori.claim_adjudication import (
    ClaimAdjudicationRequest,
    ClaimAdjudicator,
    deterministic_semantic_adjudication,
    sentence_requires_adjudication,
)
from factori.hashing import sha256_json, sha256_text
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
    ClaimSupportAuditReport,
    ClaimSupportItem,
    ControllerActionType,
    LiteraturePositioningReport,
    RetrievalRunReport,
)

CITATION_KEY_POLICY = "FirstAuthorYearShortTitle; duplicate keys receive deterministic letters."
CITATION_MARKER_RE = re.compile(r"\[@([A-Za-z0-9][A-Za-z0-9_.:-]*)\]")
_URL_RE = re.compile(r"https?://[^\s)>\]}]+", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
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
    "citations establish empirical validation",
    "citation establishes empirical validation",
    "citations verify the theorem",
    "citation verifies the theorem",
)
_CLAIM_CLASS_PROOF_PATTERNS = (
    " theorem ",
    " lemma ",
    " proposition ",
    " conjecture ",
    " proof ",
    "leanverified",
    "verified theorem",
)
_CLAIM_CLASS_EXPERIMENT_PATTERNS = (
    " empirically validated",
    " empirical validation",
    " real-world validation",
    " real world validation",
    " experiment verifies",
    " field validated",
)
_CLAIM_CLASS_NOVELTY_PATTERNS = (
    "proves novelty",
    "proven novel",
    "novelty is proven",
    "establishes novelty",
)
_CLAIM_CLASS_PUBLICATION_PATTERNS = (
    "publication ready",
    "ready for publication",
    "accepted paper",
)
_SOURCE_CONTEXT_PATTERNS = (
    "retrieved source",
    "retrieval metadata",
    "source metadata",
    "fixture source",
    "registry source",
    "citation registry",
)
_LITERATURE_BACKGROUND_PATTERNS = (
    "literature",
    "prior work",
    "background",
    "citation",
    "source-context",
    "source context",
)
_EXTERNAL_FACTUAL_PATTERNS = (
    "studies show",
    "prior work shows",
    "the literature shows",
    "field data show",
    "survey data show",
    "according to",
)
_PROBLEM_PATTERNS = (
    "problem",
    "problem framing",
    "research problem",
)
_METHOD_PATTERNS = (
    "method",
    "pipeline",
    "mechanically",
    "drafting engine",
    "orchestration",
)
_EVIDENCE_BOUNDARY_PATTERNS = (
    "no proof",
    "not a proof",
    "not proof",
    "without proof",
    "no experiment",
    "not an experiment",
    "without experiment",
    "no validation",
    "does not validate",
    "not publication ready",
    "not publication-ready",
    "not evidence",
    "not verification evidence",
    "not empirical validation",
    "not exhaustive literature coverage",
    "not proof of novelty",
    "does not provide empirical validation",
    "does not establish empirical validation",
    "do not establish empirical validation",
    "does not establish scientific validation",
    "do not establish scientific validation",
    "does not establish publication readiness",
    "do not establish publication readiness",
    "does not provide proof",
    "does not create evidence",
    "cannot create evidence",
    "separate from verification evidence",
    "bounded retrieval context",
    "bounded by available retrieval metadata",
    "bounded literature context",
    "bounded literature positioning",
    "does not transform",
    "no real proof",
    "no real experiment",
    "no real-world empirical validation",
    "no real world empirical validation",
    "no publication-ready claim",
    "evidence boundary",
    "evidence boundaries",
)
_LIMITATION_PATTERNS = (
    "limitation",
    "lacks",
    "absence of",
    "unavailable",
    "does not provide",
)
_PROVENANCE_PATTERNS = (
    "provenance",
    "artifact",
    "ledger",
    "run id",
    "audit",
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
        metadata = dict(getattr(result, "metadata", {}) or {})
        source_status = _source_status(result, metadata)
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
                retrieval_backend=str(
                    metadata.get("backend") or getattr(result, "provider", "unknown")
                ),
                retrieved_at=str(getattr(result, "retrieved_at", "1970-01-01T00:00:00Z")),
                raw_metadata_hash=str(getattr(result, "raw_metadata_hash", "")),
                source_artifact_id=source_artifact_ids.get(source_id),
                source_type=str(metadata.get("source_type", "retrieval_metadata")),
                abstract_or_snippet=(
                    getattr(result, "abstract", None)
                    or getattr(result, "snippet", None)
                    or None
                ),
                allowed_citation_key=key,
                trust_level=str(metadata.get("trust_level", "metadata_only")),
                source_status=source_status,
                support_scope=_support_scope(metadata, source_status),
                supported_topics=_supported_topics(result, metadata),
                source_snippet=(
                    getattr(result, "snippet", None)
                    or getattr(result, "abstract", None)
                    or None
                ),
                source_summary=(
                    getattr(result, "abstract", None)
                    or getattr(result, "snippet", None)
                    or None
                ),
                fixture_only=source_status == "fixture",
                may_support_background_context=bool(
                    metadata.get("may_support_background_context", True)
                ),
                may_support_method_context=bool(
                    metadata.get("may_support_method_context", False)
                ),
                may_support_empirical_claims=bool(
                    metadata.get("may_support_empirical_claims", False)
                ),
                may_support_proof_claims=bool(
                    metadata.get("may_support_proof_claims", False)
                ),
                may_support_novelty_claims=bool(
                    metadata.get("may_support_novelty_claims", False)
                ),
                warnings=warnings,
            )
        )
    bibliography = [_bibliography_entry(record) for record in records]
    warnings = sorted({warning for record in records for warning in record.warnings})
    if not records:
        warnings.append("No retrieval sources were available for citation registry construction.")
    backends = sorted({record.retrieval_backend for record in records})
    retrieval_backend = backends[0] if len(backends) == 1 else "mixed" if backends else "none"
    accepted = [record for record in records if record.source_status != "rejected"]
    return CitationRegistry(
        run_id=run_id,
        citations=records,
        bibliography=bibliography,
        citation_key_policy=CITATION_KEY_POLICY,
        citation_policy="registry-only" if records else "none",
        retrieval_backend=retrieval_backend,
        retrieval_scope="bounded-source-metadata",
        source_registry_hash=sha256_json([record.model_dump(mode="json") for record in records]),
        source_count=len(records),
        accepted_source_count=len(accepted),
        rejected_source_count=len(records) - len(accepted),
        warnings=warnings,
        fake=all(record.source_status == "fixture" for record in records) if records else True,
    )


def build_citation_registry_from_ledger(
    run_id: str,
    ledger: ResearchLedger,
    *,
    max_sources: int | None = None,
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
    ordered_results = sorted(
        results,
        key=lambda item: (item.provider, item.query, item.rank, item.source_id),
    )
    if max_sources is not None:
        ordered_results = ordered_results[:max_sources]
    return build_citation_registry(
        run_id,
        ordered_results,
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
    if not citation_registry.citations and re.search(
        r"^#{1,6}\s+(bibliography|references)\s*$",
        markdown,
        re.IGNORECASE | re.MULTILINE,
    ):
        reasons.append("bibliography section appears without citation registry sources")
    known_urls = {
        _normalized_external_identifier(record.url)
        for record in citation_registry.citations
        if record.url
    }
    known_dois = {record.doi.casefold() for record in citation_registry.citations if record.doi}
    invented_urls = sorted(
        {
            _normalized_external_identifier(url)
            for url in _URL_RE.findall(markdown)
        }
        - known_urls
    )
    invented_dois = sorted(
        doi.rstrip(".,;:")
        for doi in set(_DOI_RE.findall(markdown))
        if doi.rstrip(".,;:").casefold() not in known_dois
    )
    if invented_urls:
        reasons.append("URLs not present in citation registry: " + ", ".join(invented_urls))
    if invented_dois:
        reasons.append("DOIs not present in citation registry: " + ", ".join(invented_dois))
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
    bibliography_registry_backed = bool(citation_registry.citations) and (
        len(citation_registry.bibliography) == len(citation_registry.citations)
        and all(
            entry.has_source_provenance and entry.citation_key in key_to_record
            for entry in citation_registry.bibliography
        )
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
        citation_policy=citation_registry.citation_policy,
        citation_registry_source_count=len(citation_registry.citations),
        registry_backed_citation_count=sum(
            count for key, count in marker_counts.items() if key in key_to_record
        ),
        unregistered_citation_keys=unknown,
        bibliography_registry_backed=bibliography_registry_backed,
    )


def build_claim_support_audit(
    *,
    run_id: str,
    markdown: str,
    citation_registry: CitationRegistry | None,
    claim_adjudicator: ClaimAdjudicator | None = None,
    available_evidence_artifacts: dict[str, bool] | None = None,
) -> ClaimSupportAuditReport:
    """Build a sentence-to-source audit with semantic meaning kept separate from facts."""
    registry = citation_registry or CitationRegistry(
        run_id=run_id,
        citations=[],
        bibliography=[],
        citation_key_policy=CITATION_KEY_POLICY,
        citation_policy="none",
        source_registry_hash=sha256_json([]),
    )
    key_to_record = {record.citation_key: record for record in registry.citations}
    available_evidence = {
        "proof": False,
        "experiment": False,
        "human_review": False,
        "publication_ready": False,
        **(available_evidence_artifacts or {}),
    }
    items: list[ClaimSupportItem] = []
    placement_violations: list[str] = []
    sentence_contexts: list[dict[str, Any]] = []
    for paragraph in _paragraph_contexts(markdown):
        if _is_bibliography_section(paragraph["section_name"]):
            continue
        paragraph_markers = sorted(set(CITATION_MARKER_RE.findall(paragraph["text"])))
        for sentence_index, sentence in enumerate(_split_sentences(paragraph["text"])):
            sentence_markers = sorted(set(CITATION_MARKER_RE.findall(sentence)))
            local_markers = sorted(set(paragraph_markers))
            claim_class = classify_claim_sentence(sentence)
            if _is_appendix_context(paragraph["section_name"]) and claim_class in {
                "proof_claim",
                "experiment_claim",
                "novelty_claim",
                "publication_readiness_claim",
            }:
                claim_class = "pipeline_status_claim"
            if _is_appendix_context(paragraph["section_name"]) and claim_class in {
                "source_context_claim",
                "literature_background_claim",
            }:
                claim_class = "provenance_statement"
            sentence_id = (
                f"{_slug(paragraph['section_name'])}-"
                f"p{paragraph['paragraph_index']}-s{sentence_index}"
            )
            sentence_contexts.append(
                {
                    "sentence_id": sentence_id,
                    "section_name": paragraph["section_name"],
                    "sentence": sentence,
                    "sentence_markers": sentence_markers,
                    "paragraph_markers": local_markers,
                    "preliminary_claim_class": claim_class,
                    "paragraph_index": paragraph["paragraph_index"],
                    "sentence_index": sentence_index,
                }
            )

    risky_requests = [
        ClaimAdjudicationRequest(
            sentence_id=context["sentence_id"],
            section_name=context["section_name"],
            sentence=context["sentence"],
            preliminary_claim_class=context["preliminary_claim_class"],
            citation_keys_present=context["sentence_markers"],
            registry_source_summaries=[
                {
                    "citation_key": key,
                    "support_scope": key_to_record[key].support_scope,
                    "fixture_only": key_to_record[key].fixture_only,
                }
                for key in context["sentence_markers"]
                if key in key_to_record
            ],
            available_evidence_artifacts=available_evidence,
        )
        for context in sentence_contexts
        if sentence_requires_adjudication(context["sentence"])
        and not _is_appendix_context(context["section_name"])
    ]
    initial_adjudication_calls = (
        claim_adjudicator.call_count if claim_adjudicator is not None else 0
    )
    if claim_adjudicator is None:
        adjudications = [
            deterministic_semantic_adjudication(request) for request in risky_requests
        ]
        adjudicator_backend = "deterministic_fallback"
        adjudication_enabled = False
        adjudication_calls = 0
        adjudicator_model = None
    else:
        adjudications = claim_adjudicator.adjudicate(risky_requests)
        adjudicator_backend = claim_adjudicator.backend_name
        adjudication_enabled = True
        adjudication_calls = claim_adjudicator.call_count - initial_adjudication_calls
        adjudicator_model = claim_adjudicator.model
    adjudication_by_id = {item.sentence_id: item for item in adjudications}
    semantically_adjudicated_count = sum(
        1
        for item in adjudications
        if item.adjudicator_backend != "deterministic_fallback"
    )

    for context in sentence_contexts:
        preliminary_class = context["preliminary_claim_class"]
        adjudication = adjudication_by_id.get(context["sentence_id"])
        claim_class = (
            adjudication.adjudicated_claim_class
            if adjudication is not None
            else preliminary_class
        )
        status, reason, sources = _support_status_for_sentence(
            claim_class=claim_class,
            sentence_markers=context["sentence_markers"],
            paragraph_markers=context["paragraph_markers"],
            key_to_record=key_to_record,
            registry_present=bool(registry.citations),
            citation_use=adjudication.citation_use if adjudication else "none",
            available_evidence_artifacts=available_evidence,
        )
        items.append(
            ClaimSupportItem(
                sentence_id=context["sentence_id"],
                section_name=context["section_name"],
                sentence_text_hash=sha256_text(context["sentence"]),
                sentence_snippet=_sentence_snippet(context["sentence"]),
                claim_class=claim_class,
                citation_keys_present=context["sentence_markers"],
                required_support_type=_required_support_type(claim_class),
                supporting_source_ids=sources,
                support_status=status,
                unsupported_reason=reason,
                paragraph_index=context["paragraph_index"],
                sentence_index=context["sentence_index"],
                preliminary_claim_class=preliminary_class,
                adjudicated_claim_class=(
                    adjudication.adjudicated_claim_class if adjudication else None
                ),
                adjudication_changed_class=(
                    adjudication is not None
                    and adjudication.adjudicated_claim_class != preliminary_class
                ),
                adjudication_confidence=(adjudication.confidence if adjudication else None),
                adjudication_reasoning_brief=(
                    adjudication.reasoning_brief if adjudication else None
                ),
                citation_use=adjudication.citation_use if adjudication else "none",
            )
        )
    summary = {
        "total_sentences": len(items),
        "registry_supported": sum(
            1 for item in items if item.support_status == "registry_supported"
        ),
        "evidence_artifact_supported": sum(
            1 for item in items if item.support_status == "evidence_artifact_supported"
        ),
        "scaffold_not_required": sum(
            1 for item in items if item.support_status == "not_required_scaffold"
        ),
        "missing_required_citation": sum(
            1 for item in items if item.support_status == "missing_required_citation"
        ),
        "scope_mismatch": sum(
            1
            for item in items
            if item.support_status == "registry_key_present_but_scope_mismatch"
        ),
        "forbidden_claim": sum(
            1
            for item in items
            if item.support_status == "forbidden_claim_without_evidence"
        ),
        "unsupported_external_claim": sum(
            1 for item in items if item.support_status == "unsupported_external_claim"
        ),
        "citation_as_validation_misuse": sum(
            1 for item in items if item.support_status == "citation_as_validation_misuse"
        ),
    }
    unsupported = [
        item
        for item in items
        if item.support_status
        in {
            "registry_key_present_but_scope_mismatch",
            "missing_required_citation",
            "forbidden_claim_without_evidence",
            "unsupported_external_claim",
            "citation_as_validation_misuse",
        }
    ]
    return ClaimSupportAuditReport(
        run_id=run_id,
        citation_registry_present=bool(registry.citations),
        citation_policy=registry.citation_policy,
        claim_support_items=items,
        summary_counts=dict(sorted(summary.items())),
        unsupported_items=unsupported,
        citation_placement_violations=sorted(set(placement_violations)),
        citation_as_validation_misuse_count=sum(
            1
            for item in items
            if item.support_status == "citation_as_validation_misuse"
        ),
        claim_adjudication_enabled=adjudication_enabled,
        claim_adjudicator_backend=adjudicator_backend,
        claim_adjudicator_model=adjudicator_model,
        claim_adjudication_calls=adjudication_calls,
        adjudicated_sentence_count=semantically_adjudicated_count,
        deterministic_sentence_count=len(items) - semantically_adjudicated_count,
        adjudication_items=adjudications,
        post_adjudication_summary_counts=dict(sorted(summary.items())),
    )


def classify_claim_sentence(sentence: str) -> str:
    """Classify one manuscript sentence for deterministic citation-support checks."""
    text = f" {' '.join(sentence.casefold().split())} "
    if _contains_any(text, _CLAIM_CLASS_PUBLICATION_PATTERNS):
        return "publication_readiness_claim"
    if _contains_any(text, _CLAIM_CLASS_NOVELTY_PATTERNS):
        return "novelty_claim"
    if _contains_any(text, _EVIDENCE_BOUNDARY_PATTERNS):
        return "evidence_boundary_statement"
    if "fake lean" in text or "fake proof" in text or "fake validator" in text:
        return "pipeline_status_claim"
    if _contains_any(text, _CLAIM_CLASS_PROOF_PATTERNS):
        return "proof_claim"
    if _contains_any(text, _CLAIM_CLASS_EXPERIMENT_PATTERNS):
        return "experiment_claim"
    if _contains_any(text, _EXTERNAL_FACTUAL_PATTERNS):
        return "external_factual_claim"
    if _contains_any(text, _SOURCE_CONTEXT_PATTERNS):
        return "source_context_claim"
    if _contains_any(text, _LITERATURE_BACKGROUND_PATTERNS):
        return "literature_background_claim"
    if _contains_any(text, _LIMITATION_PATTERNS):
        return "limitation_statement"
    if _contains_any(text, _PROVENANCE_PATTERNS):
        return "provenance_statement"
    if _contains_any(text, _METHOD_PATTERNS):
        return "method_description_statement"
    if _contains_any(text, _PROBLEM_PATTERNS):
        return "problem_framing_statement"
    return "scaffold_statement"


def repair_confirmed_claim_support_violations(
    markdown: str,
    audit: ClaimSupportAuditReport,
) -> tuple[str, list[str]]:
    """Remove only sentences confirmed unsafe by the post-adjudication support audit."""
    removable_ids = {
        item.sentence_id
        for item in audit.unsupported_items
        if item.support_status
        in {
            "forbidden_claim_without_evidence",
            "citation_as_validation_misuse",
        }
    }
    if not removable_ids:
        return markdown, []
    revised = markdown
    removed: list[str] = []
    for paragraph in _paragraph_contexts(markdown):
        if _is_bibliography_section(paragraph["section_name"]):
            continue
        for sentence_index, sentence in enumerate(_split_sentences(paragraph["text"])):
            sentence_id = (
                f"{_slug(paragraph['section_name'])}-"
                f"p{paragraph['paragraph_index']}-s{sentence_index}"
            )
            if sentence_id not in removable_ids or sentence not in revised:
                continue
            revised = revised.replace(sentence, "", 1)
            removed.append(sentence_id)
    revised = re.sub(r"[ \t]+\n", "\n", revised)
    revised = re.sub(r"\n{3,}", "\n\n", revised).strip() + "\n"
    return revised, sorted(removed)


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
        "implies_publication_readiness": False,
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
            "implies_publication_readiness": False,
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


def _support_status_for_sentence(
    *,
    claim_class: str,
    sentence_markers: list[str],
    paragraph_markers: list[str],
    key_to_record: dict[str, CitationRecord],
    registry_present: bool,
    citation_use: str = "none",
    available_evidence_artifacts: dict[str, bool] | None = None,
) -> tuple[str, str | None, list[str]]:
    local_keys = sentence_markers or paragraph_markers
    known_records = [key_to_record[key] for key in local_keys if key in key_to_record]
    unknown_keys = [key for key in local_keys if key not in key_to_record]
    evidence = available_evidence_artifacts or {}
    if citation_use.startswith("misused_as_"):
        return (
            "citation_as_validation_misuse",
            f"citation use is semantically classified as {citation_use}",
            [record.source_id for record in known_records],
        )
    if claim_class in {
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    }:
        if known_records or unknown_keys:
            return (
                "citation_as_validation_misuse",
                f"citations cannot support {claim_class}",
                [record.source_id for record in known_records],
            )
        evidence_kind = {
            "proof_claim": "proof",
            "experiment_claim": "experiment",
        }.get(claim_class)
        if evidence_kind and evidence.get(evidence_kind, False):
            return (
                "evidence_artifact_supported",
                None,
                [],
            )
        return (
            "forbidden_claim_without_evidence",
            f"{claim_class} requires real evidence artifacts, not manuscript prose",
            [],
        )
    if claim_class in {
        "source_context_claim",
        "literature_background_claim",
        "external_factual_claim",
    }:
        if not registry_present and claim_class in {
            "source_context_claim",
            "literature_background_claim",
        }:
            return ("not_required_scaffold", None, [])
        if unknown_keys:
            return (
                "unsupported_external_claim",
                "citation key is not present in the registry: " + ", ".join(unknown_keys),
                [],
            )
        if not known_records:
            return (
                "missing_required_citation",
                f"{claim_class} requires a registry-backed local citation",
                [],
            )
        matched = [
            record
            for record in known_records
            if _record_supports_claim_class(record, claim_class)
        ]
        if not matched:
            return (
                "registry_key_present_but_scope_mismatch",
                f"registry source scope does not support {claim_class}",
                [record.source_id for record in known_records],
            )
        return (
            "registry_supported",
            None,
            sorted(record.source_id for record in matched),
        )
    return ("not_required_scaffold", None, [])


def _record_supports_claim_class(record: CitationRecord, claim_class: str) -> bool:
    if claim_class in {"source_context_claim", "literature_background_claim"}:
        return record.may_support_background_context
    if claim_class == "external_factual_claim":
        return (
            record.source_status != "fixture"
            and not record.fixture_only
            and record.may_support_background_context
        )
    return False


def _required_support_type(claim_class: str) -> str:
    if claim_class in {"source_context_claim", "literature_background_claim"}:
        return "registry_background_context"
    if claim_class == "external_factual_claim":
        return "registry_external_source"
    if claim_class in {
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    }:
        return "real_evidence_artifact"
    return "none"


def _paragraph_contexts(markdown: str) -> list[dict[str, Any]]:
    contexts = []
    current_section = "Preamble"
    paragraph_lines: list[str] = []
    paragraph_index = 0
    for line in markdown.splitlines():
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
        if heading_match:
            if paragraph_lines:
                text = " ".join(item.strip() for item in paragraph_lines if item.strip())
                if text:
                    contexts.append(
                        {
                            "section_name": current_section,
                            "paragraph_index": paragraph_index,
                            "text": text,
                        }
                    )
                    paragraph_index += 1
                paragraph_lines = []
            current_section = heading_match.group(1).strip()
            paragraph_index = 0
            continue
        if not line.strip():
            if paragraph_lines:
                text = " ".join(item.strip() for item in paragraph_lines if item.strip())
                if text:
                    contexts.append(
                        {
                            "section_name": current_section,
                            "paragraph_index": paragraph_index,
                            "text": text,
                        }
                    )
                    paragraph_index += 1
                paragraph_lines = []
            continue
        paragraph_lines.append(line)
    if paragraph_lines:
        text = " ".join(item.strip() for item in paragraph_lines if item.strip())
        if text:
            contexts.append(
                {
                    "section_name": current_section,
                    "paragraph_index": paragraph_index,
                    "text": text,
                }
            )
    return contexts


def _split_sentences(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`*_])", normalized)
    return [part.strip() for part in parts if part.strip()]


def _is_bibliography_section(section_name: str) -> bool:
    return section_name.strip().casefold() in {"bibliography", "references"}


def _is_appendix_context(section_name: str) -> bool:
    lowered = section_name.strip().casefold()
    return "appendix" in lowered or lowered in {"bibliography", "references"}


def _sentence_snippet(sentence: str) -> str:
    cleaned = " ".join(sentence.split())
    return cleaned[:237] + "..." if len(cleaned) > 240 else cleaned


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "section"


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


def _source_status(result: Any, metadata: dict[str, Any]) -> str:
    configured = str(metadata.get("source_status", "")).strip().lower()
    if configured in {
        "retrieved",
        "user_provided",
        "fixture",
        "rejected",
        "stale",
        "unverified_metadata",
    }:
        return configured
    if bool(getattr(result, "fake", False)) or str(
        getattr(result, "provider", "")
    ).casefold() == "fake":
        return "fixture"
    return "retrieved"


def _support_scope(metadata: dict[str, Any], source_status: str) -> list[str]:
    configured = metadata.get("support_scope")
    if isinstance(configured, list):
        scope = sorted({str(item) for item in configured if str(item).strip()})
        if scope:
            return scope
    if source_status == "fixture":
        return ["background_context", "fixture_pipeline_validation"]
    return ["background_context"]


def _supported_topics(result: Any, metadata: dict[str, Any]) -> list[str]:
    configured = metadata.get("supported_topics")
    if isinstance(configured, list):
        topics = sorted({str(item) for item in configured if str(item).strip()})
        if topics:
            return topics
    text = " ".join(
        str(value or "")
        for value in (
            getattr(result, "title", ""),
            getattr(result, "query", ""),
            getattr(result, "abstract", ""),
            getattr(result, "snippet", ""),
        )
    )
    words = [
        word.casefold()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text)
        if word.casefold()
        not in {"fixture", "metadata", "record", "source", "retrieval"}
    ]
    return sorted(dict.fromkeys(words[:8]))


def _normalized_external_identifier(value: str) -> str:
    return value.rstrip(".,;:")


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


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


__all__ = [
    "CITATION_KEY_POLICY",
    "CITATION_MARKER_RE",
    "CitationRegistryArtifacts",
    "build_citation_registry",
    "build_citation_registry_from_ledger",
    "build_claim_support_audit",
    "classify_claim_sentence",
    "validate_citation_usage",
    "write_citation_registry_reports",
]
