from __future__ import annotations

from factori.latex_safety import validate_latex_export
from factori.schemas import (
    CitationRecord,
    CitationRegistry,
    LatexExportContract,
    LatexSourceMap,
    LatexSourceMapEntry,
    VerificationLabel,
)


def test_unknown_citation_key_fails_safety_validation() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex="\\section{Introduction} Unknown \\cite{Invented2025}.",
        source_map=_source_map(citation_keys=["Invented2025"]),
        citation_registry=_registry(),
    )

    assert report.rejected
    assert report.unknown_citation_keys == ["Invented2025"]
    assert any("unknown or invented" in reason for reason in report.reasons)


def test_known_citation_key_passes_safety_validation() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex="\\section{Introduction} Known \\cite{Smith2024BoundedContext}.",
        source_map=_source_map(),
        citation_registry=_registry(),
    )

    assert report.safe
    assert not report.rejected
    assert report.used_citation_keys == ["Smith2024BoundedContext"]


def test_safety_rejects_retrieval_as_novelty_proof_language() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex=(
            "\\section{Introduction} Retrieval proves novelty for this manuscript "
            "\\cite{Smith2024BoundedContext}."
        ),
        source_map=_source_map(),
        citation_registry=_registry(),
    )

    assert report.rejected
    assert any("novelty/proof evidence" in reason for reason in report.reasons)


def test_safety_allows_explicit_retrieval_disclaimer() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex=(
            "\\section{Introduction} Retrieval does not prove novelty "
            "\\cite{Smith2024BoundedContext}."
        ),
        source_map=_source_map(),
        citation_registry=_registry(),
    )

    assert report.safe


def test_safety_rejects_synthetic_as_real_empirical_validation() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex=(
            "\\section{Results} Synthetic evidence gives real-world validation "
            "\\cite{Smith2024BoundedContext}."
        ),
        source_map=_source_map(),
        citation_registry=_registry(),
    )

    assert report.rejected
    assert any("real-world empirical validation" in reason for reason in report.reasons)


def test_latex_output_cannot_create_or_upgrade_scientific_labels() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex=(
            "\\section{Results} The result is RealDataExperimentVerified "
            "\\cite{Smith2024BoundedContext}."
        ),
        source_map=_source_map(),
        citation_registry=_registry(),
    )

    assert report.rejected
    assert report.is_verification_evidence is False
    assert report.creates_scientific_validation is False
    assert any("RealDataExperimentVerified" in reason for reason in report.reasons)


def test_source_map_unknown_claim_or_evidence_fails() -> None:
    report = validate_latex_export(
        contract=_contract(),
        paper_tex="\\section{Introduction} Known \\cite{Smith2024BoundedContext}.",
        source_map=_source_map(claim_ids=["claim-x"], evidence_ids=["evidence-x"]),
        citation_registry=_registry(),
    )

    assert report.rejected
    assert any("unknown claim IDs" in reason for reason in report.reasons)
    assert any("unknown evidence artifact IDs" in reason for reason in report.reasons)


def _contract() -> LatexExportContract:
    return LatexExportContract(
        run_id="run-1",
        manuscript_draft_artifact_id="complete-manuscript-draft",
        citation_registry_artifact_id="citation-registry",
        section_order=["Introduction"],
        source_map_policy="map sections to source claims and citations",
        allowed_citation_keys=["Smith2024BoundedContext"],
        allowed_claim_ids=["claim-main"],
        allowed_evidence_artifact_ids=["evidence-a"],
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
    )


def _source_map(
    *,
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    citation_keys: list[str] | None = None,
) -> LatexSourceMap:
    return LatexSourceMap(
        run_id="run-1",
        entries=[
            LatexSourceMapEntry(
                latex_block_id="latex-block-001",
                section_id="introduction",
                section_title="Introduction",
                claim_ids=claim_ids or ["claim-main"],
                evidence_artifact_ids=evidence_ids or ["evidence-a"],
                citation_keys=citation_keys or ["Smith2024BoundedContext"],
                markdown_line_range=[1, 3],
                latex_line_range=[1, 3],
            )
        ],
        source_map_policy="map sections to source claims and citations",
        covers_all_major_sections=True,
    )


def _registry() -> CitationRegistry:
    return CitationRegistry(
        run_id="run-1",
        citations=[
            CitationRecord(
                citation_id="citation-source-1",
                citation_key="Smith2024BoundedContext",
                source_id="source-1",
                title="Bounded context",
                authors=["Ada Smith"],
                year=2024,
                provider="fake",
                retrieved_at="1970-01-01T00:00:00.000000Z",
                raw_metadata_hash="0" * 64,
            )
        ],
        bibliography=[],
        citation_key_policy="deterministic",
        source_registry_hash="0" * 64,
    )
