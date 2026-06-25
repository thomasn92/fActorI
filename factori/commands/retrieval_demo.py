"""Library entry point for the retrieval adequacy demo."""

from __future__ import annotations

from dataclasses import dataclass

from factori.adapters.config import AdapterConfig
from factori.adapters.registry import get_adapter_registry
from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import LiteratureState, RetrievalAdequacyCertificate


@dataclass(frozen=True)
class RetrievalAdequacyDemoResult:
    """Result of the retrieval-adequacy-demo library command."""

    certificate: RetrievalAdequacyCertificate
    retrieval_backend: str
    query: str


def run_retrieval_adequacy_demo(
    *,
    query: str = "distribution shift uncertainty quantification",
    retrieval_backend: str = "fake",
    allow_external_calls: bool = False,
    retrieval_limit: int = 5,
) -> RetrievalAdequacyDemoResult:
    """Compute fake-default or explicitly gated bounded retrieval adequacy."""
    registry = get_adapter_registry(
        AdapterConfig(
            retrieval_backend=retrieval_backend,
            allow_external_calls=allow_external_calls,
            retrieval_limit=retrieval_limit,
        )
    )
    if registry.config.retrieval_backend == "fake":
        certificate = compute_retrieval_adequacy(
            LiteratureState(
                semantic=0.70,
                keyword=0.74,
                citation=0.66,
                diversity=0.62,
                adversarial=0.58,
                novelty_risk=0.25,
            )
        )
    else:
        results = registry.retrieval.search(query, retrieval_limit)
        certificate = registry.retrieval.build_adequacy_certificate(query, results)
    return RetrievalAdequacyDemoResult(
        certificate=certificate,
        retrieval_backend=registry.config.retrieval_backend,
        query=query,
    )


__all__ = ["RetrievalAdequacyDemoResult", "run_retrieval_adequacy_demo"]
