"""Explicit adapter interfaces with deterministic fake defaults."""

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
from factori.adapters.registry import (
    AdapterConfigurationError,
    AdapterRegistry,
    get_adapter_registry,
)

__all__ = [
    "AdapterConfig",
    "AdapterConfigurationError",
    "AdapterRegistry",
    "ExperimentRunner",
    "FakeExperimentRunner",
    "FakeHumanReviewClient",
    "FakeLLMClient",
    "FakeProofVerifier",
    "FakeProseGenerator",
    "FakeRetrievalClient",
    "HumanReviewClient",
    "LLMClient",
    "ProofVerifier",
    "ProseGenerator",
    "RetrievalClient",
    "get_adapter_registry",
    "load_adapter_config",
]
