from __future__ import annotations

import pytest
from pydantic import ValidationError

from factori.adapters.config import AdapterConfig, load_adapter_config
from factori.config import DEFAULT_ADAPTER_BACKEND, DEFAULT_ALLOW_EXTERNAL_CALLS


def test_default_adapter_config_is_fake_and_external_calls_are_disabled() -> None:
    config = load_adapter_config()

    assert config.adapter_backend == "fake"
    assert config.reviewer_backend == "fake"
    assert config.use_llm_reviewers is False
    assert config.allow_external_calls is False
    assert DEFAULT_ADAPTER_BACKEND == "fake"
    assert DEFAULT_ALLOW_EXTERNAL_CALLS is False


def test_adapter_config_loads_and_normalizes_mapping() -> None:
    config = load_adapter_config(
        {"adapter_backend": "  FAKE  ", "allow_external_calls": False}
    )

    assert config == AdapterConfig()


def test_adapter_config_is_strict() -> None:
    with pytest.raises(ValidationError):
        AdapterConfig(adapter_backend="fake", unknown_option=True)  # type: ignore[call-arg]


def test_adapter_config_rejects_empty_backend() -> None:
    with pytest.raises(ValidationError, match="adapter_backend must not be empty"):
        AdapterConfig(adapter_backend="   ")


def test_loading_existing_config_is_identity_preserving() -> None:
    config = AdapterConfig()

    assert load_adapter_config(config) is config
