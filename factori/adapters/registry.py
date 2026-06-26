"""Adapter registry with deterministic fake defaults and gated real adapters."""

from __future__ import annotations

import os
import shutil
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
from factori.adapters.capabilities import (
    AdapterProviderDescriptor,
    AdapterRegistryDescriptor,
    backend_names_for_kind,
    find_descriptor,
    get_provider_descriptors,
)
from factori.adapters.config import AdapterConfig, load_adapter_config
from factori.adapters.errors import (
    AdapterBackendNotFound,
    AdapterCapabilityError,
    AdapterConfigurationError,
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
)
from factori.adapters.experiment_real import (
    ExperimentToolRunner,
    LocalSyntheticExperimentRunner,
)
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
from factori.adapters.proof_real import LeanProofVerifier, ProofToolRunner
from factori.adapters.prose_real import OpenAIProseGenerator
from factori.adapters.retrieval_real import (
    OpenAlexRetrievalClient,
    OpenAlexTransport,
    RetrievalTransport,
)


@dataclass(frozen=True)
class AdapterRegistry:
    """Small explicit collection of active backend adapters."""

    config: AdapterConfig
    descriptor: AdapterRegistryDescriptor
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

    def provider_descriptors(self) -> tuple[AdapterProviderDescriptor, ...]:
        """Return provider capability metadata in stable order."""
        return self.descriptor.providers


def get_adapter_registry(
    config: AdapterConfig | Mapping[str, Any] | None = None,
    *,
    llm_transport: LLMTransport | None = None,
    reviewer_transport: LLMTransport | None = None,
    retrieval_transport: RetrievalTransport | None = None,
    retrieval_clock: Callable[[], str] | None = None,
    proof_runner: ProofToolRunner | None = None,
    experiment_tool_runner: ExperimentToolRunner | None = None,
    prose_transport: LLMTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> AdapterRegistry:
    """Build fake defaults plus explicitly gated Stage A and Stage B adapters."""
    loaded = load_adapter_config(config)
    _require_backend(
        loaded.adapter_backend,
        kind="llm",
        capability="candidate_generation",
        label="Adapter",
    )
    environment = os.environ if environ is None else environ
    llm: LLMClient = FakeLLMClient()
    if loaded.adapter_backend in {"openai", "real_llm"}:
        if not loaded.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM adapters."
            )
        configured_key = (
            loaded.api_key.get_secret_value() if loaded.api_key is not None else None
        )
        api_key = configured_key or environment.get(loaded.api_key_env)
        if not api_key:
            raise AdapterMissingCredentials(
                "Real LLM adapter requested but no API key is configured."
            )
        llm = OpenAILLMClient(
            api_key=api_key,
            model=loaded.llm_model,
            transport=llm_transport or OpenAIResponsesTransport(),
            max_candidates=loaded.llm_max_candidates,
            allow_external_calls=True,
        )
    _require_backend(
        loaded.reviewer_backend,
        kind="reviewer",
        capability="review",
        label="Reviewer",
    )
    reviewer: ReviewerClient = FakeReviewerClient()
    if loaded.use_llm_reviewers:
        if loaded.reviewer_backend == "fake":
            raise AdapterCapabilityError(
                "LLM reviewers requested but reviewer_backend is 'fake'."
            )
        if not loaded.allow_external_calls:
            raise AdapterExternalCallsDisabled(
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
            raise AdapterMissingCredentials(
                "Real LLM reviewer adapter requested but no API key is configured."
            )
        reviewer = OpenAIReviewerClient(
            api_key=reviewer_key,
            model=loaded.reviewer_model,
            transport=reviewer_transport or OpenAIResponsesTransport(),
            max_objections=loaded.reviewer_max_objections,
            allow_external_calls=True,
        )
    _require_backend(
        loaded.retrieval_backend,
        kind="retrieval",
        capability="retrieval",
        label="Retrieval",
    )
    retrieval: RetrievalClient = FakeRetrievalClient()
    if loaded.retrieval_backend in {"openalex", "real_retrieval"}:
        if not loaded.allow_external_calls:
            raise AdapterExternalCallsDisabled(
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
            raise AdapterMissingCredentials(
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
    _require_backend(
        loaded.proof_backend,
        kind="proof",
        capability="proof",
        label="Proof",
    )
    proof_verifier: ProofVerifier = FakeProofVerifier()
    if loaded.proof_backend in {"lean", "real_proof"}:
        if not loaded.allow_external_tools:
            raise AdapterExternalCallsDisabled(
                "External proof tools are disabled. Set allow_external_tools=true to use "
                "real proof adapters."
            )
        executable = loaded.proof_executable
        if not executable or (proof_runner is None and shutil.which(executable) is None):
            raise AdapterConfigurationError(
                "Real proof adapter requested but proof executable is not configured or "
                "not found."
            )
        proof_verifier = LeanProofVerifier(
            proof_executable=executable,
            runner=proof_runner,
            timeout_seconds=loaded.proof_timeout_seconds,
            allow_external_tools=True,
        )
    _require_backend(
        loaded.experiment_backend,
        kind="experiment",
        capability="experiments",
        label="Experiment",
    )
    experiment_runner: ExperimentRunner = FakeExperimentRunner()
    if loaded.experiment_backend in {"local_synthetic", "real_experiment"}:
        if not loaded.allow_external_tools:
            raise AdapterExternalCallsDisabled(
                "External experiment tools are disabled. Set allow_external_tools=true to use "
                "real experiment adapters."
            )
        runner_name = loaded.experiment_runner
        if not runner_name or (
            experiment_tool_runner is None and shutil.which(runner_name) is None
        ):
            raise AdapterConfigurationError(
                "Real experiment adapter requested but experiment runner is not configured or "
                "not found."
            )
        experiment_runner = LocalSyntheticExperimentRunner(
            runner_name=runner_name,
            runner=experiment_tool_runner,
            timeout_seconds=loaded.experiment_timeout_seconds,
            replications=loaded.experiment_replications,
            allow_external_tools=True,
        )
    _require_backend(
        loaded.prose_backend,
        kind="prose",
        capability="prose_generation",
        label="Prose",
    )
    prose_generator: ProseGenerator = FakeProseGenerator()
    if loaded.prose_backend in {"openai", "real_prose"}:
        if not loaded.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "prose adapters."
            )
        configured_prose_key = (
            loaded.prose_api_key.get_secret_value()
            if loaded.prose_api_key is not None
            else None
        )
        prose_key = configured_prose_key or environment.get(loaded.prose_api_key_env)
        if not prose_key:
            raise AdapterMissingCredentials(
                "Real prose adapter requested but no API key is configured."
            )
        prose_generator = OpenAIProseGenerator(
            api_key=prose_key,
            model=loaded.prose_model,
            transport=prose_transport or OpenAIResponsesTransport(),
            allow_external_calls=True,
        )
    return AdapterRegistry(
        config=loaded,
        descriptor=AdapterRegistryDescriptor(
            active_candidate_backend=loaded.adapter_backend,
            active_reviewer_backend=loaded.reviewer_backend,
            active_retrieval_backend=loaded.retrieval_backend,
            active_proof_backend=loaded.proof_backend,
            active_experiment_backend=loaded.experiment_backend,
            active_prose_backend=loaded.prose_backend,
            allow_external_calls=loaded.allow_external_calls,
            allow_external_tools=loaded.allow_external_tools,
            providers=get_provider_descriptors(),
        ),
        llm=llm,
        retrieval=retrieval,
        reviewer=reviewer,
        proof_verifier=proof_verifier,
        experiment_runner=experiment_runner,
        prose_generator=prose_generator,
        human_review=FakeHumanReviewClient(),
    )


def _require_backend(
    backend: str,
    *,
    kind: str,
    capability: str,
    label: str,
) -> AdapterProviderDescriptor:
    descriptor = find_descriptor(backend, kind=kind, capability=capability)
    if descriptor is None:
        accepted = ", ".join(backend_names_for_kind(kind))
        raise AdapterBackendNotFound(
            f"{label} backend '{backend}' is not implemented. "
            f"Available {kind} backends are: {accepted}."
        )
    return descriptor


__all__ = [
    "AdapterBackendNotFound",
    "AdapterCapabilityError",
    "AdapterConfigurationError",
    "AdapterExternalCallsDisabled",
    "AdapterMissingCredentials",
    "AdapterRegistry",
    "get_adapter_registry",
]
