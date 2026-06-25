from __future__ import annotations

from factori.adapters.retrieval_sources import (
    build_retrieval_query,
    normalize_retrieval_result,
    normalize_retrieved_document,
)

RETRIEVED_AT = "2026-01-02T03:04:05Z"


def test_retrieval_query_construction_is_deterministic() -> None:
    first = build_retrieval_query("  graph   curvature geography ", 5)
    second = build_retrieval_query("graph curvature geography", 5)

    assert first == second
    assert first.provider == "openalex"
    assert first.parameters["search"] == "graph curvature geography"
    assert "api_key" not in first.parameters
    assert first.is_verification_evidence is False
    assert first.proves_novelty is False


def test_openalex_result_normalization_is_deterministic_and_provenanced() -> None:
    raw = {
        **_raw_work(),
        "_query": "graph curvature geography",
        "_rank": 0,
        "_retrieved_at": RETRIEVED_AT,
        "_normalized_score": 0.93,
    }

    first = normalize_retrieval_result(raw, "openalex", backend="provider-neutral")
    second = normalize_retrieval_result(raw, "openalex", backend="provider-neutral")

    assert first == second
    assert first.source_id == "W123"
    assert first.provider == "openalex"
    assert first.authors == ["Ada Researcher", "Ben Scholar"]
    assert first.year == 2024
    assert first.venue == "Journal of Controlled Examples"
    assert first.abstract == "Graph curvature supports spatial diagnostics"
    assert first.url == "https://example.org/work/123"
    assert first.doi == "10.1234/example.123"
    assert len(first.raw_metadata_hash) == 64
    assert first.source_provenance.raw_metadata_hash == first.raw_metadata_hash
    assert first.source_provenance.query == first.query
    assert first.is_verification_evidence is False
    assert first.proves_novelty is False
    assert first.metadata["adapter_backend"] == "provider-neutral"
    assert first.metadata["adapter_provider"] == "openalex"


def test_fetched_document_has_raw_payload_hash_and_boundary_markers() -> None:
    document = normalize_retrieved_document(
        _raw_work(),
        "openalex",
        backend="provider-neutral",
        retrieved_at=RETRIEVED_AT,
    )

    assert document.source_id == "W123"
    assert document.provider == "openalex"
    assert len(document.raw_payload_hash) == 64
    assert document.text_or_abstract == "Graph curvature supports spatial diagnostics"
    assert document.fetch_status == "MetadataOrAbstractFetched"
    assert document.is_verification_evidence is False
    assert document.proves_novelty is False
    assert document.metadata["adapter_backend"] == "provider-neutral"
    assert document.metadata["adapter_provider"] == "openalex"


def _raw_work(work_id: str = "W123") -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "display_name": "Graph curvature for spatial diagnostics",
        "authorships": [
            {"author": {"display_name": "Ada Researcher"}},
            {"author": {"display_name": "Ben Scholar"}},
        ],
        "publication_year": 2024,
        "primary_location": {
            "source": {"display_name": "Journal of Controlled Examples"},
            "landing_page_url": "HTTPS://EXAMPLE.ORG/work/123#fragment",
        },
        "doi": "https://doi.org/10.1234/Example.123",
        "abstract_inverted_index": {
            "Graph": [0],
            "curvature": [1],
            "supports": [2],
            "spatial": [3],
            "diagnostics": [4],
        },
        "type": "article",
        "cited_by_count": 12,
    }
