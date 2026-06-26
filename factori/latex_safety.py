"""Safety checks for deterministic LaTeX export artifacts."""

from __future__ import annotations

import re

from factori.schemas import (
    CitationRegistry,
    LatexExportContract,
    LatexSafetyReport,
    LatexSourceMap,
)

LATEX_CITATION_RE = re.compile(r"\\cite\{([^}]+)\}")

_RETRIEVAL_PROOF_CLAIMS = (
    "retrieval proves novelty",
    "retrieval proves",
    "citations prove novelty",
    "citation proves novelty",
    "novelty is proven by retrieval",
    "novelty is proven by citations",
    "retrieval as proof",
    "citation as proof",
)
_SYNTHETIC_REAL_WORLD_CLAIMS = (
    "synthetic evidence gives real-world validation",
    "synthetic experiment gives real-world validation",
    "synthetic experiment proves real-world",
    "synthetic-only evidence validates real-world",
    "synthetic evidence is empirical validation",
)
_NEGATIONS = (
    "not ",
    "cannot ",
    "does not ",
    "do not ",
    "never ",
    "without ",
)


def validate_latex_export(
    *,
    contract: LatexExportContract,
    paper_tex: str,
    source_map: LatexSourceMap,
    citation_registry: CitationRegistry | None = None,
) -> LatexSafetyReport:
    """Validate LaTeX/citation/source-map boundaries without changing artifacts."""
    registry_keys = (
        {record.citation_key for record in citation_registry.citations}
        if citation_registry is not None
        else set()
    )
    allowed_keys = set(contract.allowed_citation_keys)
    known_keys = registry_keys or allowed_keys
    used_keys = _citation_keys_in_latex(paper_tex)
    unknown_keys = sorted(key for key in used_keys if key not in known_keys)
    allowed_claims = set(contract.allowed_claim_ids)
    allowed_evidence = set(contract.allowed_evidence_artifact_ids)
    reasons: list[str] = []
    warnings: list[str] = []

    if unknown_keys:
        reasons.append("unknown or invented LaTeX citation keys: " + ", ".join(unknown_keys))
    for entry in source_map.entries:
        extra_claims = sorted(set(entry.claim_ids) - allowed_claims)
        if extra_claims:
            reasons.append(
                f"{entry.latex_block_id}: unknown claim IDs in source map: "
                + ", ".join(extra_claims)
            )
        extra_evidence = sorted(set(entry.evidence_artifact_ids) - allowed_evidence)
        if extra_evidence:
            reasons.append(
                f"{entry.latex_block_id}: unknown evidence artifact IDs in source map: "
                + ", ".join(extra_evidence)
            )
        extra_citations = sorted(set(entry.citation_keys) - known_keys)
        if extra_citations:
            reasons.append(
                f"{entry.latex_block_id}: unknown citation keys in source map: "
                + ", ".join(extra_citations)
            )

    if not source_map.covers_all_major_sections:
        reasons.append(
            "source map is missing major sections: "
            + ", ".join(source_map.missing_sections)
        )
    for label in contract.forbidden_labels:
        if label.value in paper_tex:
            reasons.append(f"forbidden scientific label appears in LaTeX: {label.value}")
    if "RealDataExperimentVerified" in paper_tex:
        reasons.append("RealDataExperimentVerified is unavailable in the MVP")
    lowered = " ".join(paper_tex.lower().split())
    if _contains_unbounded_claim(lowered, _RETRIEVAL_PROOF_CLAIMS):
        reasons.append("LaTeX describes retrieval or citations as novelty/proof evidence")
    if _contains_unbounded_claim(lowered, _SYNTHETIC_REAL_WORLD_CLAIMS):
        reasons.append("LaTeX describes synthetic evidence as real-world empirical validation")
    if citation_registry is not None:
        incomplete = sorted(
            entry.citation_key
            for entry in citation_registry.bibliography
            if not entry.has_source_provenance
        )
        if incomplete:
            warnings.append(
                "bibliography entries have incomplete source provenance: "
                + ", ".join(incomplete)
            )
    if not used_keys and contract.allowed_citation_keys:
        warnings.append("No LaTeX citation commands were emitted despite available citations.")

    return LatexSafetyReport(
        run_id=contract.run_id,
        safe=not reasons,
        rejected=bool(reasons),
        reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
        used_citation_keys=used_keys,
        unknown_citation_keys=unknown_keys,
        source_map_sections=[entry.section_id for entry in source_map.entries],
        missing_source_map_sections=source_map.missing_sections,
    )


def _citation_keys_in_latex(paper_tex: str) -> list[str]:
    keys = []
    for match in LATEX_CITATION_RE.findall(paper_tex):
        keys.extend(key.strip() for key in match.split(",") if key.strip())
    return sorted(set(keys))


def _contains_unbounded_claim(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        start = text.find(phrase)
        while start >= 0:
            prefix = text[max(0, start - 20) : start]
            if not any(negation in prefix for negation in _NEGATIONS):
                return True
            start = text.find(phrase, start + len(phrase))
    return False


__all__ = [
    "LATEX_CITATION_RE",
    "validate_latex_export",
]
