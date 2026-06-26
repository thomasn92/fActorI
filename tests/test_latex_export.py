from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.cli import app
from factori.hashing import sha256_file
from factori.latex_export import (
    build_latex_export_contract,
    build_references_bib,
    export_markdown_draft_to_latex,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    LatexExportContract,
    LatexExportResult,
    LatexSafetyReport,
    LatexSourceMap,
    LatexSourceMapEntry,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    ManuscriptDraftStatus,
    PipelineRunConfig,
    PipelineStage,
    ProseSectionContract,
    SectionDraftingTask,
    SectionDraftSafetySummary,
)


def test_latex_export_models_are_importable() -> None:
    assert LatexExportContract
    assert LatexSourceMap
    assert LatexSourceMapEntry
    assert LatexExportResult
    assert LatexSafetyReport


def test_latex_export_from_simple_markdown_is_deterministic() -> None:
    first = _export()
    second = _export()

    assert first == second
    assert "\\section{Introduction}" in first.paper_tex
    assert "\\cite{Smith2024BoundedContext}" in first.paper_tex
    assert first.safety_report.safe


def test_markdown_subset_converts_to_latex_safely() -> None:
    result = _export(
        markdown=(
            "# Demo & draft\n\n"
            "## Introduction\n\n"
            "This uses 50% of $x_1$ and [@Smith2024BoundedContext].\n\n"
            "$$x^2 + y^2 = z^2$$\n\n"
            "- item_one\n"
            "- item_two\n\n"
            "1. first\n"
            "2. second\n\n"
            "```\n"
            "x_1 <- 2\n"
            "```\n"
        )
    )

    assert "\\title{Demo \\& draft}" in result.paper_tex
    assert "50\\%" in result.paper_tex
    assert "$x_1$" in result.paper_tex
    assert "\\[" in result.paper_tex
    assert "x^2 + y^2 = z^2" in result.paper_tex
    assert "\\begin{itemize}" in result.paper_tex
    assert "\\begin{enumerate}" in result.paper_tex
    assert "\\begin{verbatim}" in result.paper_tex


def test_source_map_contains_sections_claims_evidence_and_citations() -> None:
    result = _export()
    entry = result.source_map.entries[0]

    assert entry.section_id == "introduction"
    assert entry.claim_ids == ["claim-main"]
    assert entry.evidence_artifact_ids == ["evidence-a"]
    assert entry.citation_keys == ["Smith2024BoundedContext"]
    assert result.source_map.covers_all_major_sections


def test_bibliography_entries_are_generated_only_from_citation_records() -> None:
    registry = _citation_registry()
    bib, warnings = build_references_bib(registry)

    assert "@misc{Smith2024BoundedContext" in bib
    assert "Bounded context for synthetic claims" in bib
    assert "Source: source-1; provider: fake" in bib
    assert warnings == []


def test_incomplete_bibliography_metadata_warns_without_inventing_values() -> None:
    record = CitationRecord(
        citation_id="citation-source-2",
        citation_key="Source2",
        source_id="source-2",
        title="Incomplete source",
        authors=[],
        year=None,
        provider="fake",
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash="0" * 64,
    )
    registry = CitationRegistry(
        run_id="run-1",
        citations=[record],
        bibliography=[
            BibliographyEntry(
                citation_id=record.citation_id,
                citation_key=record.citation_key,
                source_id=record.source_id,
                markdown="- [@Source2] Incomplete source.",
                has_source_provenance=True,
            )
        ],
        citation_key_policy="deterministic",
        source_registry_hash="0" * 64,
    )

    bib, warnings = build_references_bib(registry)

    assert "Unknown" not in bib
    assert "n.d." not in bib
    assert warnings == [
        "Source2: missing author metadata",
        "Source2: missing year metadata",
    ]


def test_export_latex_cli_works_without_latex_installed_and_write_report(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-1",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    runner = CliRunner()
    draft = runner.invoke(
        app,
        [
            "draft-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )
    assert draft.exit_code == 0, draft.output

    result = runner.invoke(
        app,
        [
            "export-latex",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    export_result = payload["latex_export_result"]
    assert export_result["safety_report"]["safe"] is True
    artifacts = payload["artifacts"]
    for key in ("paper", "references", "source_map", "export_report", "safety_report"):
        ref = ArtifactRef.model_validate(artifacts[key])
        assert (tmp_path / ref.path).is_file()
        assert ref.content_hash == sha256_file(tmp_path / ref.path)
        linked = ArtifactRef.model_validate_json(
            (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
        )
        assert linked.metadata["is_verification_evidence"] is False
        assert linked.metadata["creates_scientific_validation"] is False
    assert artifacts["paper"]["path"].endswith("latex/paper.tex")
    assert artifacts["references"]["path"].endswith("latex/references.bib")


def _export(markdown: str | None = None) -> LatexExportResult:
    plan = _drafting_plan()
    report = _drafting_report()
    registry = _citation_registry()
    contract = build_latex_export_contract(
        run_id="run-1",
        manuscript_draft_artifact_id="complete-manuscript-draft",
        drafting_plan=plan,
        drafting_report=report,
        citation_registry=registry,
        citation_registry_artifact_id="citation-registry",
    )
    return export_markdown_draft_to_latex(
        run_id="run-1",
        draft_markdown=markdown or _markdown(),
        contract=contract,
        drafting_plan=plan,
        drafting_report=report,
        citation_registry=registry,
    )


def _markdown() -> str:
    return (
        "# Demo manuscript\n\n"
        "## Introduction\n\n"
        "This draft cites bounded retrieval context [@Smith2024BoundedContext].\n\n"
        "## Claim/Evidence Appendix\n\n"
        "- `claim-main`: evidence-a\n"
    )


def _drafting_plan() -> ManuscriptDraftingPlan:
    contract = ProseSectionContract(
        run_id="run-1",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=["claim-main"],
        allowed_evidence_artifact_ids=["evidence-a"],
        allowed_citation_ids=["citation-source-1"],
        allowed_citation_keys=["Smith2024BoundedContext"],
        evidence_boundary_instructions=["Draft only."],
        citation_boundary_instructions=["Use only allowed citation keys."],
        style_instructions=["Placeholder only."],
        max_words=120,
        source_contract_hashes={"claim_table": "0" * 64},
    )
    return ManuscriptDraftingPlan(
        run_id="run-1",
        plan_id="drafting-plan",
        manuscript_plan_id="manuscript-plan",
        narrative_contract_id="narrative-contract",
        paper_shape_critique_id="paper-shape",
        sections_count=1,
        tasks=[
            SectionDraftingTask(
                section_id="introduction",
                section_title="Introduction",
                section_role="Introduction",
                allowed_claim_ids=["claim-main"],
                allowed_evidence_artifact_ids=["evidence-a"],
                allowed_citation_ids=["citation-source-1"],
                allowed_citation_keys=["Smith2024BoundedContext"],
                source_contract_hashes={"claim_table": "0" * 64},
                prose_contract=contract,
            )
        ],
    )


def _drafting_report() -> ManuscriptDraftingReport:
    return ManuscriptDraftingReport(
        run_id="run-1",
        drafting_plan_id="drafting-plan",
        sections_total=1,
        sections_safe=1,
        sections_unsafe=0,
        draft_status=ManuscriptDraftStatus.DRAFT_COMPLETE,
        section_summaries=[
            SectionDraftSafetySummary(
                section_id="introduction",
                safety_status="Safe",
                safe=True,
                rejected=False,
                used_claim_ids=["claim-main"],
                used_evidence_artifact_ids=["evidence-a"],
                used_citation_ids=["citation-source-1"],
                used_citation_keys=["Smith2024BoundedContext"],
            )
        ],
        manuscript_draft_artifact_id="complete-manuscript-draft",
    )


def _citation_registry() -> CitationRegistry:
    record = CitationRecord(
        citation_id="citation-source-1",
        citation_key="Smith2024BoundedContext",
        source_id="source-1",
        title="Bounded context for synthetic claims",
        authors=["Ada Smith"],
        year=2024,
        venue="Fake Venue",
        provider="fake",
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash="0" * 64,
        source_artifact_id="retrieval-normalized-results-source-1",
    )
    return CitationRegistry(
        run_id="run-1",
        citations=[record],
        bibliography=[
            BibliographyEntry(
                citation_id=record.citation_id,
                citation_key=record.citation_key,
                source_id=record.source_id,
                markdown="- [@Smith2024BoundedContext] Ada Smith (2024).",
                has_source_provenance=True,
            )
        ],
        citation_key_policy="deterministic",
        source_registry_hash="0" * 64,
    )
