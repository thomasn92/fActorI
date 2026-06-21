"""Adapter registry with deterministic fake defaults and gated real LLM support."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from factori.adapters.base import (
    ExperimentRunner,
    HumanReviewClient,
    LLMClient,
    ProofVerifier,
    ProseGenerator,
    RetrievalClient,
    ReviewerClient,
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
from factori.adapters.llm_real import (
    LLMTransport,
    OpenAILLMClient,
    OpenAIResponsesTransport,
)
from factori.adapters.llm_review import FakeReviewerClient, OpenAIReviewerClient
from factori.adapters.retrieval_real import (
    OpenAlexRetrievalClient,
    OpenAlexTransport,
    RetrievalTransport,
)


class AdapterConfigurationError(ValueError):
    """Raised when an unavailable or unsafe adapter backend is requested."""


@dataclass(frozen=True)
class AdapterRegistry:
    """Small explicit collection of active backend adapters."""

    config: AdapterConfig
    llm: LLMClient
    retrieval: RetrievalClient
    reviewer: ReviewerClient
    proof_verifier: ProofVerifier
    experiment_runner: ExperimentRunner
    prose_generator: ProseGenerator
    human_review: HumanReviewClient

    def class_names(self) -> dict[str, str]:
        """Return deterministic adapter class names for inspection and reports."""
        return {
            "llm": type(self.llm).__name__,
            "retrieval": type(self.retrieval).__name__,
            "reviewer": type(self.reviewer).__name__,
            "proof_verifier": type(self.proof_verifier).__name__,
            "experiment_runner": type(self.experiment_runner).__name__,
            "prose_generator": type(self.prose_generator).__name__,
            "human_review": type(self.human_review).__name__,
        }


def get_adapter_registry(
    config: AdapterConfig | Mapping[str, Any] | None = None,
    *,
    llm_transport: LLMTransport | None = None,
    reviewer_transport: LLMTransport | None = None,
    retrieval_transport: RetrievalTransport | None = None,
    retrieval_clock: Callable[[], str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AdapterRegistry:
    """Build fake defaults plus explicitly gated Stage A and Stage B adapters."""
    loaded = load_adapter_config(config)
    if loaded.adapter_backend not in {"fake", "openai", "real_llm"}:
        raise AdapterConfigurationError(
            f"Adapter backend '{loaded.adapter_backend}' is not implemented. "
            "Only 'fake' is available in this milestone."
        )
    environment = os.environ if environ is None else environ
    llm: LLMClient = FakeLLMClient()
    if loaded.adapter_backend in {"openai", "real_llm"}:
        if not loaded.allow_external_calls:
            raise AdapterConfigurationError(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM adapters."
            )
        configured_key = (
            loaded.api_key.get_secret_value() if loaded.api_key is not None else None
        )
        api_key = configured_key or environment.get(loaded.api_key_env)
        if not api_key:
            raise AdapterConfigurationError(
                "Real LLM adapter requested but no API key is configured."
            )
        llm = OpenAILLMClient(
            api_key=api_key,
            model=loaded.llm_model,
            transport=llm_transport or OpenAIResponsesTransport(),
            max_candidates=loaded.llm_max_candidates,
            allow_external_calls=True,
        )
    if loaded.reviewer_backend not in {"fake", "openai", "real_llm"}:
        raise AdapterConfigurationError(
            f"Reviewer backend '{loaded.reviewer_backend}' is not implemented. "
            "Available reviewer backends are 'fake' and gated 'openai'."
        )
    reviewer: ReviewerClient = FakeReviewerClient()
    if loaded.use_llm_reviewers:
        if loaded.reviewer_backend == "fake":
            raise AdapterConfigurationError(
                "LLM reviewers requested but reviewer_backend is 'fake'."
            )
        if not loaded.allow_external_calls:
            raise AdapterConfigurationError(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM reviewer adapters."
            )
        configured_reviewer_key = (
            loaded.reviewer_api_key.get_secret_value()
            if loaded.reviewer_api_key is not None
            else None
        )
        reviewer_key = configured_reviewer_key or environment.get(
            loaded.reviewer_api_key_env
        )
        if not reviewer_key:
            raise AdapterConfigurationError(
                "Real LLM reviewer adapter requested but no API key is configured."
            )
        reviewer = OpenAIReviewerClient(
            api_key=reviewer_key,
            model=loaded.reviewer_model,
            transport=reviewer_transport or OpenAIResponsesTransport(),
            max_objections=loaded.reviewer_max_objections,
            allow_external_calls=True,
        )
    if loaded.retrieval_backend not in {"fake", "openalex", "real_retrieval"}:
        raise AdapterConfigurationError(
            f"Retrieval backend '{loaded.retrieval_backend}' is not implemented. "
            "Available retrieval backends are 'fake' and gated 'openalex'."
        )
    retrieval: RetrievalClient = FakeRetrievalClient()
    if loaded.retrieval_backend in {"openalex", "real_retrieval"}:
        if not loaded.allow_external_calls:
            raise AdapterConfigurationError(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "retrieval adapters."
            )
        configured_retrieval_key = (
            loaded.retrieval_api_key.get_secret_value()
            if loaded.retrieval_api_key is not None
            else None
        )
        retrieval_key = configured_retrieval_key or environment.get(
            loaded.retrieval_api_key_env
        )
        if not retrieval_key:
            raise AdapterConfigurationError(
                "Real retrieval adapter requested but required credentials are not configured."
            )
        retrieval_kwargs: dict[str, Any] = {}
        if retrieval_clock is not None:
            retrieval_kwargs["clock"] = retrieval_clock
        retrieval = OpenAlexRetrievalClient(
            api_key=retrieval_key,
            transport=retrieval_transport or OpenAlexTransport(),
            default_limit=loaded.retrieval_limit,
            allow_external_calls=True,
            **retrieval_kwargs,
        )
    return AdapterRegistry(
        config=loaded,
        llm=llm,
        retrieval=retrieval,
        reviewer=reviewer,
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
