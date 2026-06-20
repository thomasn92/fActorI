"""Deterministic adapter registry with fake-only backend enforcement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factori.adapters.base import (
    ExperimentRunner,
    HumanReviewClient,
    LLMClient,
    ProofVerifier,
    ProseGenerator,
    RetrievalClient,
)
from factori.adapters.config import AdapterConfig, load_adapter_config
from factori.adapters.fake import (
    FakeExperimentRunner,
    FakeHumanReviewClient,
    FakeLLMClient,
    FakeProofVerifier,
    FakeProseGenerator,
    FakeRetrievalClient,
)


class AdapterConfigurationError(ValueError):
    """Raised when an unavailable or unsafe adapter backend is requested."""


@dataclass(frozen=True)
class AdapterRegistry:
    """Small explicit collection of active backend adapters."""

    config: AdapterConfig
    llm: LLMClient
    retrieval: RetrievalClient
    proof_verifier: ProofVerifier
    experiment_runner: ExperimentRunner
    prose_generator: ProseGenerator
    human_review: HumanReviewClient

    def class_names(self) -> dict[str, str]:
        """Return deterministic adapter class names for inspection and reports."""
        return {
            "llm": type(self.llm).__name__,
            "retrieval": type(self.retrieval).__name__,
            "proof_verifier": type(self.proof_verifier).__name__,
            "experiment_runner": type(self.experiment_runner).__name__,
            "prose_generator": type(self.prose_generator).__name__,
            "human_review": type(self.human_review).__name__,
        }


def get_adapter_registry(
    config: AdapterConfig | Mapping[str, Any] | None = None,
) -> AdapterRegistry:
    """Build the active adapter registry; only fake is available in this milestone."""
    loaded = load_adapter_config(config)
    if loaded.adapter_backend != "fake":
        raise AdapterConfigurationError(
            f"Adapter backend '{loaded.adapter_backend}' is not implemented. "
            "Only 'fake' is available in this milestone."
        )
    return AdapterRegistry(
        config=loaded,
        llm=FakeLLMClient(),
        retrieval=FakeRetrievalClient(),
        proof_verifier=FakeProofVerifier(),
        experiment_runner=FakeExperimentRunner(),
        prose_generator=FakeProseGenerator(),
        human_review=FakeHumanReviewClient(),
    )


__all__ = [
    "AdapterConfigurationError",
    "AdapterRegistry",
    "get_adapter_registry",
]
