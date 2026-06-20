from __future__ import annotations

from factori.adapters.base import (
    ExperimentRunner,
    HumanReviewClient,
    LLMClient,
    ProofVerifier,
    ProseGenerator,
    RetrievalClient,
)
from factori.adapters.fake import (
    FakeExperimentRunner,
    FakeHumanReviewClient,
    FakeLLMClient,
    FakeProofVerifier,
    FakeProseGenerator,
    FakeRetrievalClient,
)


def test_all_adapter_interfaces_are_importable_and_runtime_checkable() -> None:
    assert isinstance(FakeLLMClient(), LLMClient)
    assert isinstance(FakeRetrievalClient(), RetrievalClient)
    assert isinstance(FakeProofVerifier(), ProofVerifier)
    assert isinstance(FakeExperimentRunner(), ExperimentRunner)
    assert isinstance(FakeProseGenerator(), ProseGenerator)
    assert isinstance(FakeHumanReviewClient(), HumanReviewClient)


def test_all_fake_adapters_disable_external_calls() -> None:
    adapters = [
        FakeLLMClient(),
        FakeRetrievalClient(),
        FakeProofVerifier(),
        FakeExperimentRunner(),
        FakeProseGenerator(),
        FakeHumanReviewClient(),
    ]

    assert all(adapter.backend_name == "fake" for adapter in adapters)
    assert all(adapter.is_fake for adapter in adapters)
    assert all(not adapter.external_calls_enabled for adapter in adapters)
