from __future__ import annotations

from factori.adapters.capabilities import (
    backend_names_for_kind,
    find_descriptor,
    get_provider_descriptors,
    known_retrieval_providers,
)


def test_provider_descriptors_are_deterministic() -> None:
    first = get_provider_descriptors()
    second = get_provider_descriptors()

    assert first == second
    assert [descriptor.backend_name for descriptor in first] == [
        "fake",
        "openai",
        "openai",
        "openalex",
    ]


def test_provider_descriptors_include_required_capability_flags() -> None:
    fake = find_descriptor("fake", kind="llm", capability="candidate_generation")
    openai = find_descriptor("openai", kind="llm", capability="candidate_generation")
    reviewer = find_descriptor("openai", kind="reviewer", capability="review")
    openalex = find_descriptor("openalex", kind="retrieval", capability="retrieval")

    assert fake is not None
    assert fake.is_fake is True
    assert fake.is_default is True
    assert openai is not None
    assert openai.requires_external_calls is True
    assert openai.requires_api_key is True
    assert reviewer is not None
    assert reviewer.supports_review is True
    assert openalex is not None
    assert openalex.supports_retrieval is True
    assert openalex.requires_api_key is True


def test_backend_aliases_are_available_for_supported_kinds() -> None:
    assert "real_llm" in backend_names_for_kind("llm")
    assert "real_retrieval" in backend_names_for_kind("retrieval")
    assert find_descriptor("real_llm", kind="llm", capability="candidate_generation")
    assert find_descriptor("real_retrieval", kind="retrieval", capability="retrieval")


def test_known_retrieval_providers_come_from_descriptors() -> None:
    assert known_retrieval_providers() == frozenset({"fake", "openalex"})
