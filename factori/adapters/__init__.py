"""Explicit adapter interfaces with deterministic fake defaults."""

from factori.adapters.base import (
    CandidateGenerationClient,
    ContextSummarizationClient,
    ExperimentRunner,
    HumanReviewClient,
    LLMClient,
    ProofVerifier,
    ProseGenerator,
    RetrievalClient,
    ReviewerClient,
)
from factori.adapters.capabilities import (
    AdapterCapability,
    AdapterProviderDescriptor,
    AdapterRegistryDescriptor,
    get_provider_descriptors,
)
from factori.adapters.config import AdapterConfig, load_adapter_config
from factori.adapters.errors import (
    AdapterBackendNotFound,
    AdapterCapabilityError,
    AdapterConfigurationError,
    AdapterError,
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
    AdapterSafetyError,
    AdapterTransportError,
)
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
from factori.adapters.llm_review import FakeReviewerClient, OpenAIReviewerClient
from factori.adapters.llm_safety import (
    parse_llm_candidate_response,
    validate_llm_candidate,
)
from factori.adapters.registry import (
    AdapterRegistry,
    get_adapter_registry,
)
from factori.adapters.retrieval_real import OpenAlexRetrievalClient, OpenAlexTransport
from factori.adapters.retrieval_safety import validate_retrieval_result
from factori.adapters.retrieval_sources import (
    build_retrieval_query,
    normalize_retrieval_result,
)
from factori.adapters.reviewer_prompts import build_stage_b_reviewer_prompt
from factori.adapters.reviewer_safety import (
    parse_llm_reviewer_response,
    validate_llm_reviewer_report,
)

__all__ = [
    "AdapterConfig",
    "AdapterBackendNotFound",
    "AdapterCapability",
    "AdapterCapabilityError",
    "AdapterConfigurationError",
    "AdapterError",
    "AdapterExternalCallsDisabled",
    "AdapterMissingCredentials",
    "AdapterProviderDescriptor",
    "AdapterRegistryDescriptor",
    "AdapterResponseParseError",
    "AdapterSafetyError",
    "AdapterTransportError",
    "AdapterRegistry",
    "CandidateGenerationClient",
    "ContextSummarizationClient",
    "ExperimentRunner",
    "FakeExperimentRunner",
    "FakeHumanReviewClient",
    "FakeLLMClient",
    "FakeProofVerifier",
    "FakeProseGenerator",
    "FakeRetrievalClient",
    "FakeReviewerClient",
    "HumanReviewClient",
    "LLMClient",
    "OpenAILLMClient",
    "OpenAIResponsesTransport",
    "OpenAIReviewerClient",
    "OpenAlexRetrievalClient",
    "OpenAlexTransport",
    "ProofVerifier",
    "ProseGenerator",
    "RetrievalClient",
    "ReviewerClient",
    "get_adapter_registry",
    "get_provider_descriptors",
    "build_stage_a_candidate_prompt",
    "build_stage_b_reviewer_prompt",
    "build_retrieval_query",
    "load_adapter_config",
    "parse_llm_candidate_response",
    "parse_llm_reviewer_response",
    "normalize_retrieval_result",
    "validate_llm_candidate",
    "validate_llm_reviewer_report",
    "validate_retrieval_result",
]
