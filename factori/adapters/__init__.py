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
from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.adapters.llm_real import OpenAILLMClient, OpenAIResponsesTransport
from factori.adapters.llm_safety import (
    parse_llm_candidate_response,
    validate_llm_candidate,
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
    "OpenAILLMClient",
    "OpenAIResponsesTransport",
    "ProofVerifier",
    "ProseGenerator",
    "RetrievalClient",
    "get_adapter_registry",
    "build_stage_a_candidate_prompt",
    "load_adapter_config",
    "parse_llm_candidate_response",
    "validate_llm_candidate",
]
