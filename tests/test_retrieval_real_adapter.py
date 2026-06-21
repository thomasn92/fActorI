from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.registry import AdapterConfigurationError, get_adapter_registry
from factori.adapters.retrieval_real import OpenAlexRetrievalClient
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.schemas import ConstraintSet, ControllerActionType
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c_selection import run_stage_c_selection

RETRIEVED_AT = "2026-01-02T03:04:05Z"


@dataclass
class StubRetrievalTransport:
    search_response: Any
    fetch_response: Any
    search_calls: list[dict[str, Any]] = field(default_factory=list)
    fetch_calls: list[dict[str, Any]] = field(default_factory=list)

    def search(self, **kwargs: Any) -> Any:
        self.search_calls.append(kwargs)
        return self.search_response

    def fetch(self, **kwargs: Any) -> Any:
        self.fetch_calls.append(kwargs)
        return self.fetch_response


def test_fake_retrieval_is_registry_default() -> None:
    registry = get_adapter_registry()

    assert registry.config.retrieval_backend == "fake"
    assert type(registry.retrieval).__name__ == "FakeRetrievalClient"
    assert registry.retrieval.external_calls_enabled is False


def test_real_retrieval_fails_before_transport_when_external_calls_disabled() -> None:
    transport = StubRetrievalTransport(_search_response(), _raw_work(1))

    with pytest.raises(AdapterConfigurationError, match="External calls are disabled"):
        get_adapter_registry(
            AdapterConfig(
                retrieval_backend="openalex",
                allow_external_calls=False,
                retrieval_api_key="test-key",
            ),
            retrieval_transport=transport,
        )

    assert transport.search_calls == []
    assert transport.fetch_calls == []


def test_real_retrieval_fails_without_required_credentials() -> None:
    with pytest.raises(AdapterConfigurationError, match="required credentials"):
        get_adapter_registry(
            AdapterConfig(
                retrieval_backend="openalex",
                allow_external_calls=True,
            ),
            environ={},
        )


def test_openalex_client_uses_injected_transport_deterministically() -> None:
    transport = StubRetrievalTransport(_search_response(), _raw_work(1))
    client = OpenAlexRetrievalClient(
        api_key="test-key",
        transport=transport,
        allow_external_calls=True,
        clock=lambda: RETRIEVED_AT,
    )

    first = client.search("graph curvature geography", 5)
    second = client.search("graph curvature geography", 5)
    document = client.fetch(first[0].source_id)
    certificate = client.build_adequacy_certificate("graph curvature geography", first)

    assert first == second
    assert len(transport.search_calls) == 2
    assert len(transport.fetch_calls) == 1
    assert all(result.provider == "openalex" for result in first)
    assert all(len(result.raw_metadata_hash) == 64 for result in first)
    assert len(document.raw_payload_hash) == 64
    assert document.is_verification_evidence is False
    assert certificate.fake is False
    assert certificate.source_count == 5
    assert certificate.bounded_signal is True
    assert certificate.proves_novelty is False
    assert certificate.claims_literature_coverage is False
    assert any("not proof of novelty" in item for item in certificate.limitations)


def test_real_adequacy_uses_result_count_and_metadata_diversity() -> None:
    transport = StubRetrievalTransport(_search_response(), _raw_work(1))
    client = OpenAlexRetrievalClient(
        api_key="test-key",
        transport=transport,
        allow_external_calls=True,
        clock=lambda: RETRIEVED_AT,
    )
    results = client.search("human geography graph curvature", 5)

    sparse = client.build_adequacy_certificate(
        "human geography graph curvature", results[:1]
    )
    diverse = client.build_adequacy_certificate(
        "human geography graph curvature", results
    )

    assert diverse.source_count > sparse.source_count
    assert diverse.diversity >= sparse.diversity
    assert diverse.rho_adequacy > sparse.rho_adequacy


def test_stage_b_real_retrieval_writes_ledgered_non_evidence_artifacts(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    transport = StubRetrievalTransport(_search_response(), _raw_work(1))
    client = OpenAlexRetrievalClient(
        api_key="test-key",
        transport=transport,
        allow_external_calls=True,
        clock=lambda: RETRIEVED_AT,
    )

    result = run_stage_b(
        run_id="run-1",
        store=store,
        ledger=ledger,
        retrieval_client=client,
    )

    assert len(result.retrieval_runs) == len(result.stage_a_survivors)
    assert len(result.retrieval_artifacts) == len(result.stage_a_survivors) * 4
    assert all(artifact.content_hash for artifact in result.retrieval_artifacts)
    assert all(artifact.producing_commit_hash for artifact in result.retrieval_artifacts)
    assert all(
        not artifact.is_mvp_verification_evidence()
        for artifact in result.retrieval_artifacts
    )
    commits = ledger.list_commits("run-1")
    retrieval_commits = [
        commit
        for commit in commits
        if commit.action_type == ControllerActionType.RETRIEVAL_RUN_RECORDED
    ]
    assert len(retrieval_commits) == len(result.stage_a_survivors)

    manifest = build_artifact_manifest("run-1", store)
    retrieval_entries = [
        entry
        for entry in manifest.artifacts
        if entry.metadata.get("artifact_role") == "retrieval_context"
    ]
    assert len(retrieval_entries) == len(result.retrieval_artifacts)
    assert all(not entry.is_evidence for entry in retrieval_entries)
    assert all(entry.is_presentation for entry in retrieval_entries)
    report_text = (tmp_path / result.report_artifact.path).read_text(encoding="utf-8")
    assert "Retrieval adapter: openalex" in report_text
    assert "not proof of novelty" in report_text

    selection = run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    assert all(
        report.retrieval_certificate.provider == "openalex"
        for report in selection.redteam_reports.values()
    )


def test_stage_b_cli_real_retrieval_requires_explicit_opt_in_without_mutation(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    before = len(ledger.list_commits("run-1"))

    result = CliRunner().invoke(
        app,
        [
            "run-stage-b",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--retrieval-backend",
            "openalex",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr
    assert len(ledger.list_commits("run-1")) == before


def test_invalid_retrieval_backend_fails_clearly() -> None:
    with pytest.raises(AdapterConfigurationError, match="Retrieval backend 'unknown'"):
        get_adapter_registry(AdapterConfig(retrieval_backend="unknown"))


def test_retrieval_demo_real_backend_requires_explicit_opt_in() -> None:
    result = CliRunner().invoke(
        app,
        [
            "retrieval-adequacy-demo",
            "--retrieval-backend",
            "openalex",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr


def _search_response() -> dict[str, object]:
    return {"results": [_raw_work(index) for index in range(1, 6)]}


def _raw_work(index: int) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/W{index}",
        "display_name": f"Human geography graph curvature method {index}",
        "authorships": [
            {"author": {"display_name": f"Author {index}A"}},
            {"author": {"display_name": f"Author {index}B"}},
        ],
        "publication_year": 2019 + index,
        "primary_location": {
            "source": {"display_name": f"Venue {index}"},
            "landing_page_url": f"https://example.org/work/{index}",
        },
        "doi": f"10.1234/example.{index}",
        "abstract_inverted_index": {
            "Human": [0],
            "geography": [1],
            "graph": [2],
            "curvature": [3],
            "method": [4],
        },
        "type": "article",
        "cited_by_count": index * 5,
    }
