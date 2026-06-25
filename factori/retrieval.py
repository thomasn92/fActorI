"""Deterministic retrieval adequacy certificate skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    ControllerActionType,
    LiteratureState,
    RetrievalAdequacyCertificate,
    RetrievalRunReport,
)

if TYPE_CHECKING:
    from factori.adapters.base import RetrievalClient
    from factori.schemas import RetrievalResult

TAU_ADEQUACY = 0.80
DEFAULT_RETRIEVAL_WEIGHTS = {
    "semantic": 0.20,
    "keyword": 0.20,
    "citation": 0.20,
    "diversity": 0.20,
    "adversarial": 0.20,
}


@dataclass(frozen=True)
class RetrievalExecutionResult:
    """One ledgered real retrieval execution and its context artifacts."""

    report: RetrievalRunReport
    artifacts: dict[str, ArtifactRef]
    commit_hash: str


def compute_retrieval_adequacy(
    literature_state: LiteratureState,
    *,
    tau_adequacy: float = TAU_ADEQUACY,
    weights: dict[str, float] | None = None,
    retrieval_client: RetrievalClient | None = None,
    query: str = "",
    results: list[RetrievalResult] | None = None,
) -> RetrievalAdequacyCertificate:
    """Compute a deterministic placeholder retrieval adequacy certificate."""
    if retrieval_client is not None:
        adapter_results = (
            results
            if results is not None
            else retrieval_client.search(query, max(literature_state.k, 5))
        )
        certificate = retrieval_client.build_adequacy_certificate(query, adapter_results)
        if certificate.proves_novelty or certificate.claims_literature_coverage:
            raise ValueError(
                "retrieval adequacy cannot prove novelty or complete literature coverage"
            )
        return certificate

    weights = weights or DEFAULT_RETRIEVAL_WEIGHTS
    rho_adequacy = round(
        weights["semantic"] * literature_state.semantic
        + weights["keyword"] * literature_state.keyword
        + weights["citation"] * literature_state.citation
        + weights["diversity"] * literature_state.diversity
        + weights["adversarial"] * literature_state.adversarial,
        6,
    )
    passed = rho_adequacy >= tau_adequacy
    return RetrievalAdequacyCertificate(
        semantic=literature_state.semantic,
        keyword=literature_state.keyword,
        citation=literature_state.citation,
        diversity=literature_state.diversity,
        adversarial=literature_state.adversarial,
        weights=dict(weights),
        rho_adequacy=rho_adequacy,
        tau_adequacy=tau_adequacy,
        passed=passed,
        status=BranchStatus.ACTIVE
        if passed
        else BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY,
    )


def run_retrieval_with_provenance(
    *,
    run_id: str,
    query: str,
    limit: int,
    retrieval_client: RetrievalClient,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RetrievalExecutionResult:
    """Run gated retrieval and ledger its non-verification context artifacts."""
    if retrieval_client.is_fake:
        raise ValueError("real retrieval provenance requires a non-fake retrieval client")
    trace_count = len(getattr(retrieval_client, "generation_traces", []))
    results = retrieval_client.search(query, limit)
    traces = list(getattr(retrieval_client, "generation_traces", []))
    if len(traces) != trace_count + 1:
        raise RuntimeError("retrieval client did not expose one sanitized generation trace")
    trace = traces[-1]
    certificate = compute_retrieval_adequacy(
        LiteratureState(k=len(results)),
        retrieval_client=retrieval_client,
        query=query,
        results=results,
    )
    report = RetrievalRunReport(
        query=trace.query,
        results=results,
        parse_report=trace.parse_report,
        certificate=certificate,
        backend=retrieval_client.backend_name,
        provider=str(getattr(retrieval_client, "provider", retrieval_client.backend_name)),
        config_metadata={
            "limit": limit,
            "external_calls_enabled": retrieval_client.external_calls_enabled,
            "credentials_recorded": False,
        },
    )
    query_hash = trace.query.query_id
    metadata = {
        "stage": "stage_b",
        "adapter_backend": retrieval_client.backend_name,
        "provider": report.provider,
        "artifact_role": "retrieval_context",
        "is_verification_evidence": False,
        "proves_novelty": False,
        "claims_literature_coverage": False,
        "fake": False,
    }
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=f"retrieval-query-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.query,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-response-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.raw_response,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-normalized-results-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload={
                    "results": results,
                    "parse_report": trace.parse_report,
                    "is_verification_evidence": False,
                },
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-adequacy-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=certificate,
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        commit_payload=report.model_dump(mode="json"),
    )
    linked = dict(
        zip(
            ["query", "response", "normalized_results", "adequacy"],
            result.artifacts,
            strict=True,
        )
    )
    return RetrievalExecutionResult(
        report=report,
        artifacts=linked,
        commit_hash=result.commit.commit_hash,
    )
