from __future__ import annotations

import pytest

from factori.adapters.retrieval_safety import (
    RetrievalResponseError,
    parse_retrieval_response,
    validate_retrieval_result,
)
from factori.adapters.retrieval_sources import normalize_retrieval_result

RETRIEVED_AT = "2026-01-02T03:04:05Z"


def test_retrieval_validation_is_deterministic() -> None:
    result = normalize_retrieval_result(
        _enriched_work(),
        "openalex",
        backend="provider-neutral-retrieval",
    )

    first = validate_retrieval_result(result)
    second = validate_retrieval_result(result)

    assert first == second
    assert first.valid
    assert result.metadata["adapter_backend"] == "provider-neutral-retrieval"
    assert result.metadata["adapter_provider"] == "openalex"


def test_malformed_results_are_rejected_with_parse_report() -> None:
    results, report = parse_retrieval_response(
        {"results": [{"display_name": "Missing source id"}, "not-an-object"]},
        provider="openalex",
        backend="provider-neutral-retrieval",
        query="controlled query",
        limit=5,
        retrieved_at=RETRIEVED_AT,
    )

    assert results == []
    assert len(report.rejected_results) == 2
    assert len(report.raw_response_hash) == 64
    assert report.is_verification_evidence is False


def test_response_without_results_collection_fails_clearly() -> None:
    with pytest.raises(RetrievalResponseError, match="results list"):
        parse_retrieval_response(
            {"meta": {}},
            provider="openalex",
            query="controlled query",
            limit=5,
            retrieved_at=RETRIEVED_AT,
        )


def test_validation_rejects_unknown_provider_and_boundary_inflation() -> None:
    result = normalize_retrieval_result(_enriched_work(), "openalex").model_copy(
        update={
            "provider": "unknown",
            "is_verification_evidence": True,
            "proves_novelty": True,
        }
    )

    validation = validate_retrieval_result(result)

    assert not validation.valid
    assert any("unknown retrieval provider" in reason for reason in validation.reasons)
    assert any("cannot be verification evidence" in reason for reason in validation.reasons)
    assert any("cannot prove novelty" in reason for reason in validation.reasons)


def _enriched_work() -> dict[str, object]:
    return {
        "id": "https://openalex.org/W123",
        "display_name": "Controlled retrieval source",
        "authorships": [{"author": {"display_name": "Ada Researcher"}}],
        "publication_year": 2024,
        "primary_location": {
            "source": {"display_name": "Example Venue"},
            "landing_page_url": "https://example.org/source",
        },
        "doi": "10.1234/example",
        "abstract_inverted_index": {"Controlled": [0], "source": [1]},
        "_query": "controlled query",
        "_rank": 0,
        "_retrieved_at": RETRIEVED_AT,
        "_normalized_score": 0.9,
    }
