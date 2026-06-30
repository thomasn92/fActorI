from __future__ import annotations

import json
from pathlib import Path

from factori.adapters.fake import FakeRetrievalClient
from factori.artifacts import ArtifactStore
from factori.citations import (
    build_citation_registry,
    build_citation_registry_from_ledger,
    build_claim_support_audit,
    classify_claim_sentence,
    validate_citation_usage,
    write_citation_registry_reports,
)
from factori.ledger import ResearchLedger
from factori.literature_positioning import build_literature_positioning_report
from factori.retrieval import (
    run_fixture_retrieval_with_provenance,
    run_local_retrieval_with_provenance,
    score_retrieval_sources,
)
from factori.schemas import (
    CitationRecord,
    CitationRegistry,
    CitationSafetyReport,
    CitationUsage,
    ControllerActionType,
    NarrativeManuscriptContract,
    RetrievalResult,
    SourceProvenance,
)
from factori.source_relevance import FakeSourceRelevanceAdjudicator

HASH = "0" * 64
LOCAL_SOURCE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "retrieval" / "human_geography_sources.json"
)
OPENALEX_STYLE_SOURCE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "retrieval"
    / "openalex_style_human_geography_sources.json"
)


def test_citation_registry_models_are_importable() -> None:
    assert CitationRecord.__name__ == "CitationRecord"
    assert CitationRegistry.__name__ == "CitationRegistry"
    assert CitationSafetyReport.__name__ == "CitationSafetyReport"
    assert CitationUsage.__name__ == "CitationUsage"


def test_claim_classes_classify_scaffold_as_not_requiring_citation() -> None:
    assert (
        classify_claim_sentence(
            "This draft is manuscript context only and does not create evidence."
        )
        == "evidence_boundary_statement"
    )


def test_bounded_literature_positioning_sentence_is_evidence_boundary() -> None:
    assert (
        classify_claim_sentence(
            "Literature positioning is bounded by available retrieval metadata."
        )
        == "evidence_boundary_statement"
    )


def test_negated_empirical_validation_sentence_is_evidence_boundary() -> None:
    assert (
        classify_claim_sentence(
            "MVP and synthetic outputs do not establish empirical validation, "
            "scientific validation, or publication readiness."
        )
        == "evidence_boundary_statement"
    )


def test_source_context_claim_requires_registry_backed_citation() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=f"## Introduction\n\nRetrieval metadata gives bounded context [@{key}].",
        citation_registry=registry,
    )

    assert audit.summary_counts["registry_supported"] == 1
    assert audit.unsupported_items == []
    assert audit.is_verification_evidence is False


def test_external_factual_claim_without_citation_is_flagged() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown="## Introduction\n\nPrior work shows a broad field trend.",
        citation_registry=registry,
    )

    assert audit.summary_counts["missing_required_citation"] == 1
    assert audit.unsupported_items[0].support_status == "missing_required_citation"


def test_citation_in_same_paragraph_supports_local_source_context_claim() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=(
            "## Introduction\n\nThe citation registry records bounded source context. "
            f"This paragraph includes the local source [@{key}]."
        ),
        citation_registry=registry,
    )

    assert audit.summary_counts["registry_supported"] >= 1


def test_bibliography_citation_does_not_support_body_claim() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=(
            "## Introduction\n\nPrior work shows a broad field trend.\n\n"
            f"## Bibliography\n\n- [@{key}] fixture entry."
        ),
        citation_registry=registry,
    )

    assert audit.summary_counts["missing_required_citation"] == 1


def test_citation_in_unrelated_section_does_not_support_body_claim() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=(
            "## Introduction\n\nPrior work shows a broad field trend.\n\n"
            f"## Limitations\n\nRetrieval metadata is bounded [@{key}]."
        ),
        citation_registry=registry,
    )

    assert audit.summary_counts["missing_required_citation"] == 1
    assert audit.summary_counts["registry_supported"] == 1


def test_provenance_appendix_metadata_does_not_require_citations() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=(
            "## Provenance Appendix\n\n"
            "- Run ID: `run-1`\n"
            "- Citation registry: `abc123`\n"
            "- Retrieval-backed citations are bounded literature context, not novelty proof."
        ),
        citation_registry=registry,
    )

    assert audit.summary_counts["missing_required_citation"] == 0
    assert audit.summary_counts["forbidden_claim"] == 0
    assert audit.unsupported_items == []


def test_unregistered_citation_key_is_unsupported_external_claim() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown="## Introduction\n\nRetrieval metadata gives context [@Invented2026].",
        citation_registry=registry,
    )

    assert audit.unsupported_items[0].support_status == "unsupported_external_claim"


def test_fixture_source_scope_mismatch_for_external_factual_claim() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=f"## Introduction\n\nPrior work shows a broad field trend [@{key}].",
        citation_registry=registry,
    )

    assert audit.summary_counts["scope_mismatch"] == 1


def test_fixture_source_cannot_support_proof_experiment_or_novelty_claims() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    audit = build_claim_support_audit(
        run_id="run-1",
        markdown=(
            f"## Introduction\n\nTheorem 1 is verified [@{key}]. "
            f"The result is empirically validated [@{key}]. "
            f"Retrieval proves novelty [@{key}]."
        ),
        citation_registry=registry,
    )

    assert audit.summary_counts["citation_as_validation_misuse"] == 3
    assert all(
        item.support_status == "citation_as_validation_misuse"
        for item in audit.unsupported_items
    )


def test_citation_key_generation_is_deterministic() -> None:
    first = build_citation_registry("run-1", [_source("S1"), _source("S2")])
    second = build_citation_registry("run-1", [_source("S1"), _source("S2")])

    assert first == second
    assert [record.citation_key for record in first.citations] == [
        "Smith2024BoundedRetrievalContexta",
        "Smith2024BoundedRetrievalContextb",
    ]


def test_missing_author_and_year_use_stable_fallback() -> None:
    registry = build_citation_registry(
        "run-1",
        [_source("S1", title="Untitled reference", authors=[], year=None)],
    )

    assert registry.citations[0].citation_key == "SourceNoYearUntitledReference"
    assert any("no authors" in warning for warning in registry.warnings)
    assert any("no year" in warning for warning in registry.warnings)


def test_citation_registry_preserves_source_provenance() -> None:
    registry = build_citation_registry(
        "run-1",
        [_source("S1", provider="fake-provider")],
        source_artifact_ids={"S1": "retrieval-normalized-results-example"},
    )
    record = registry.citations[0]

    assert record.provider == "fake-provider"
    assert record.raw_metadata_hash == HASH
    assert record.source_artifact_id == "retrieval-normalized-results-example"
    assert registry.is_verification_evidence is False
    assert registry.proves_novelty is False


def test_fixture_sources_have_explicit_bounded_registry_metadata() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    record = registry.citations[0]

    assert record.source_status == "fixture"
    assert record.allowed_citation_key == record.citation_key
    assert registry.citation_policy == "registry-only"
    assert registry.source_count == 1
    assert registry.accepted_source_count == 1
    assert registry.creates_scientific_validation is False
    assert registry.implies_publication_readiness is False


def test_fixture_retrieval_is_ledgered_without_external_calls(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "fixture-run" / "ledger.sqlite")

    result = run_fixture_retrieval_with_provenance(
        run_id="fixture-run",
        query="human geography bounded context",
        limit=3,
        retrieval_client=FakeRetrievalClient(),
        store=store,
        ledger=ledger,
    )

    assert len(result.report.results) == 3
    assert all(item.metadata["source_status"] == "fixture" for item in result.report.results)
    assert result.report.config_metadata["external_calls_enabled"] is False
    assert result.artifacts["report"].path.endswith("reports/retrieval-report.json")
    assert result.artifacts["report"].metadata["is_verification_evidence"] is False
    registry = build_citation_registry_from_ledger(
        "fixture-run",
        ledger,
        max_sources=2,
    )
    assert len(registry.citations) == 2
    assert registry.retrieval_backend == "fake"


def test_local_retrieval_backend_loads_and_filters_sources(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "local-run" / "ledger.sqlite")

    result = run_local_retrieval_with_provenance(
        run_id="local-run",
        query="human geography bounded literature context",
        limit=8,
        local_path=LOCAL_SOURCE_FIXTURE,
        store=store,
        ledger=ledger,
    )

    assert result.report.backend == "local"
    assert len(result.report.results) == 6
    assert result.artifacts["quality_report"].path.endswith(
        "reports/retrieval-quality-report.json"
    )
    accepted = [item for item in result.report.results if item.accepted_for_registry]
    rejected = [item for item in result.report.results if not item.accepted_for_registry]
    assert len(accepted) == 3
    assert len(rejected) == 3
    assert {item.rejection_reason for item in rejected} == {
        "duplicate_source",
        "low_relevance",
        "metadata_incomplete",
    }
    assert result.report.config_metadata["external_calls_enabled"] is False
    assert result.artifacts["quality_report"].metadata["is_verification_evidence"] is False


def test_rejected_local_sources_do_not_enter_citation_registry(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "local-registry" / "ledger.sqlite")
    retrieval = run_local_retrieval_with_provenance(
        run_id="local-registry",
        query="human geography bounded literature context",
        limit=8,
        local_path=LOCAL_SOURCE_FIXTURE,
        store=store,
        ledger=ledger,
    )

    registry = build_citation_registry_from_ledger("local-registry", ledger)

    accepted_count = sum(
        1 for result in retrieval.report.results if result.accepted_for_registry
    )
    assert registry.source_count == accepted_count == 3
    assert registry.accepted_source_count == 3
    assert registry.rejected_source_count == 3
    assert registry.retrieval_backend == "local"
    assert all(record.accepted_for_registry for record in registry.citations)
    assert all(record.source_status != "rejected" for record in registry.citations)
    assert all(record.relevance_score is not None for record in registry.citations)
    assert not any(
        record.source_id == "local-irrelevant-001" for record in registry.citations
    )


def test_openalex_style_local_fixture_normalizes_and_adjudicates_sources(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "openalex-local" / "ledger.sqlite")
    adjudicator = FakeSourceRelevanceAdjudicator(model="test-source-model")

    result = run_local_retrieval_with_provenance(
        run_id="openalex-local",
        query="human geography bounded literature context",
        limit=8,
        local_path=OPENALEX_STYLE_SOURCE_FIXTURE,
        store=store,
        ledger=ledger,
        source_relevance_adjudicator=adjudicator,
        source_relevance_adjudicator_model="test-source-model",
        domain="human geography",
        candidate_title_or_problem="human geography bounded context",
    )

    quality = result.report.config_metadata
    report = result.report.results
    quality_report_path = tmp_path / "runs" / "openalex-local" / "reports" / (
        "retrieval-quality-report.json"
    )
    quality_report = json.loads(quality_report_path.read_text(encoding="utf-8"))

    assert quality["external_calls_enabled"] is False
    assert len(report) == 5
    assert {item.provider for item in report} == {"openalex"}
    assert report[0].retrieval_backend == "local"
    assert adjudicator.call_count == 1
    assert quality_report["source_relevance_adjudication_enabled"] is True


def test_fake_source_relevance_accepts_borderline_and_rejects_irrelevant(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "source-adjudication" / "ledger.sqlite")
    adjudicator = FakeSourceRelevanceAdjudicator(model="test-source-model")

    retrieval = run_local_retrieval_with_provenance(
        run_id="source-adjudication",
        query="human geography bounded literature context",
        limit=8,
        local_path=OPENALEX_STYLE_SOURCE_FIXTURE,
        store=store,
        ledger=ledger,
        source_relevance_adjudicator=adjudicator,
        domain="human geography",
        candidate_title_or_problem="human geography bounded context",
    )
    quality = retrieval.artifacts["quality_report"]
    registry = build_citation_registry_from_ledger("source-adjudication", ledger)

    by_id = {item.source_id: item for item in retrieval.report.results}
    assert by_id["W560000002"].accepted_for_registry is True
    assert by_id["W560000003"].accepted_for_registry is False
    assert by_id["W560000003"].rejection_reason == "low_relevance"
    assert by_id["W560000004"].accepted_for_registry is False
    assert by_id["W560000004"].rejection_reason == "duplicate_source"
    assert by_id["W560000005"].accepted_for_registry is False
    assert by_id["W560000005"].rejection_reason == "metadata_incomplete"
    assert quality.metadata["is_verification_evidence"] is False
    assert registry.source_count == 2
    assert {record.source_id for record in registry.citations} == {
        "W560000001",
        "W560000002",
    }
    assert all(record.accepted_for_registry for record in registry.citations)


def test_hard_rejects_cannot_be_overridden_by_fake_source_adjudicator() -> None:
    scored, quality = score_retrieval_sources(
        run_id="run-source-hard-reject",
        retrieval_backend="local",
        query="human geography bounded literature context",
        domain="human geography",
        candidate_title_or_problem="human geography bounded context",
        source_relevance_adjudicator=FakeSourceRelevanceAdjudicator(),
        results=[
            _source(
                "accepted",
                title="Human Geography Bounded Literature Context",
                authors=["Ada Smith"],
                year=2024,
                provider="local",
            ).model_copy(
                update={
                    "abstract": "Human geography bounded literature context source metadata.",
                    "snippet": (
                        "Human geography bounded literature context source metadata "
                        "for background filtering."
                    ),
                }
            ),
            _source(
                "duplicate",
                title="Human Geography Bounded Literature Context",
                authors=["Ada Smith"],
                year=2024,
                provider="local",
            ).model_copy(
                update={
                    "abstract": "Duplicate human geography source metadata.",
                    "snippet": (
                        "Duplicate human geography bounded literature context source "
                        "metadata for background filtering."
                    ),
                }
            ),
            _source(
                "incomplete",
                title="Human Geography Metadata Note",
                authors=[],
                year=None,
                provider="local",
            ),
        ],
    )

    by_id = {item.source_id: item for item in scored}
    assert by_id["duplicate"].accepted_for_registry is False
    assert by_id["duplicate"].rejection_reason == "duplicate_source"
    assert by_id["incomplete"].accepted_for_registry is False
    assert by_id["incomplete"].rejection_reason == "metadata_incomplete"
    assert quality.hard_reject_count == 2
    assert quality.source_relevance_adjudication_enabled is True
    assert {item.adjudicated_relevance_label for item in quality.adjudication_items} >= {
        "duplicate",
        "metadata_insufficient",
    }


def test_citation_to_rejected_local_source_key_fails() -> None:
    scored, quality = score_retrieval_sources(
        run_id="run-1",
        retrieval_backend="local",
        query="human geography bounded literature context",
        results=[
            _source(
                "accepted",
                title="Human Geography Bounded Literature Context",
                authors=["Ada Smith"],
                year=2024,
                provider="local",
            ).model_copy(
                update={
                    "abstract": (
                        "Human geography bounded literature context source metadata "
                        "for registry background filtering."
                    ),
                    "snippet": (
                        "Human geography bounded literature context source metadata "
                        "for registry background filtering."
                    ),
                }
            ),
            _source(
                "rejected",
                title="Marine Coral Reef Thermal Stress Monitoring",
                authors=["Ira Ocean"],
                year=2019,
                provider="local",
            ).model_copy(
                update={
                    "abstract": (
                        "Marine biology records describe coral reef thermal stress "
                        "and ocean monitoring."
                    ),
                    "snippet": (
                        "Marine biology records describe coral reef thermal stress "
                        "and ocean monitoring."
                    ),
                }
            ),
        ],
    )
    registry = build_citation_registry("run-1", scored)
    rejected_key = "Ocean2019MarineCoralReef"
    report = validate_citation_usage(
        f"This paragraph cites rejected local metadata [@{rejected_key}].",
        registry,
    )

    assert quality.rejected_source_count == 1
    assert registry.source_count == 1
    assert report.rejected
    assert rejected_key in report.unknown_citation_keys


def test_citation_usage_validator_accepts_known_citation_keys() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    report = validate_citation_usage(f"This paragraph cites bounded context [@{key}].", registry)

    assert report.safe
    assert not report.rejected
    assert report.used_citation_keys == [key]
    assert report.used_citation_ids == [registry.citations[0].citation_id]


def test_citation_usage_validator_rejects_unknown_citation_keys() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    report = validate_citation_usage("This paragraph cites [@Invented2024].", registry)

    assert report.rejected
    assert report.unknown_citation_keys == ["Invented2024"]
    assert any("invented citation keys" in reason for reason in report.reasons)


def test_citation_usage_validator_rejects_invented_bibliography_entries() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    draft = "## Bibliography\n\n- [@MadeUp2026] Fabricated entry."

    report = validate_citation_usage(draft, registry)

    assert report.rejected
    assert report.invented_bibliography_keys == ["MadeUp2026"]


def test_citation_usage_validator_rejects_invented_source_metadata() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    report = validate_citation_usage(
        "## Bibliography\n\nSource metadata: https://invented.invalid/source.",
        registry,
    )

    assert report.rejected
    assert any("URLs not present" in reason for reason in report.reasons)


def test_citation_usage_validator_rejects_exhaustive_coverage_claims() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    report = validate_citation_usage(
        f"This is a comprehensive literature review covering all prior work [@{key}].",
        registry,
    )

    assert report.rejected
    assert any("exhaustive" in reason for reason in report.reasons)


def test_citation_usage_validator_rejects_retrieval_as_proof_language() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    report = validate_citation_usage(
        f"Retrieval proves novelty for this manuscript [@{key}].",
        registry,
    )

    assert report.rejected
    assert any("novelty/proof" in reason for reason in report.reasons)


def test_citation_reports_are_content_hashed_and_non_evidence(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    registry = build_citation_registry("run-1", [_source("S1")])
    positioning = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=_narrative_contract(),
    )
    safety = validate_citation_usage(positioning.markdown_intro_paragraph, registry)

    artifacts = write_citation_registry_reports(
        run_id="run-1",
        store=store,
        ledger=ledger,
        citation_registry=registry,
        literature_positioning_report=positioning,
        citation_safety_report=safety,
    )

    commits = ledger.list_commits("run-1")
    assert commits[-1].action_type == ControllerActionType.CITATION_REGISTRY_WRITTEN
    for artifact in (
        artifacts.citation_registry_artifact,
        artifacts.literature_positioning_artifact,
        artifacts.citation_safety_artifact,
    ):
        assert len(artifact.content_hash) == 64
        assert artifact.producing_commit_hash == artifacts.commit_hash
        assert artifact.metadata["is_verification_evidence"] is False
        assert artifact.metadata["proves_novelty"] is False


def _source(
    source_id: str,
    *,
    title: str = "Bounded retrieval context",
    authors: list[str] | None = None,
    year: int | None = 2024,
    provider: str = "fake",
) -> RetrievalResult:
    provenance = SourceProvenance(
        source_id=source_id,
        provider=provider,
        query="bounded retrieval context",
        rank=0,
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash=HASH,
    )
    return RetrievalResult(
        source_id=source_id,
        title=title,
        authors=authors if authors is not None else ["Ada Smith"],
        year=year,
        provider=provider,
        retrieved_at=provenance.retrieved_at,
        query=provenance.query,
        rank=0,
        raw_metadata_hash=HASH,
        source_provenance=provenance,
        fake=True,
    )


def _narrative_contract() -> NarrativeManuscriptContract:
    return NarrativeManuscriptContract(
        contract_id="narrative-contract",
        run_id="run-1",
        central_message="A bounded deterministic example.",
        problem_statement="State the problem.",
        literature_gap="The gap is bounded by retrieval metadata.",
        novelty_claim="Novelty is not proven by retrieval.",
    )
