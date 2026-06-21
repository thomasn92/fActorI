"""Strict configuration for the adapter interface layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, SecretStr, field_validator

from factori.config import (
    DEFAULT_ADAPTER_BACKEND,
    DEFAULT_ALLOW_EXTERNAL_CALLS,
    DEFAULT_LLM_MODEL,
    DEFAULT_RETRIEVAL_BACKEND,
    DEFAULT_RETRIEVAL_LIMIT,
    DEFAULT_REVIEWER_BACKEND,
    DEFAULT_REVIEWER_MAX_OBJECTIONS,
    OPENAI_API_KEY_ENV,
    OPENALEX_API_KEY_ENV,
)
from factori.schemas import StrictModel


class AdapterConfig(StrictModel):
    """Adapter selection with external access disabled by default."""

    adapter_backend: str = Field(default=DEFAULT_ADAPTER_BACKEND, min_length=1)
    allow_external_calls: bool = DEFAULT_ALLOW_EXTERNAL_CALLS
    llm_model: str = Field(default=DEFAULT_LLM_MODEL, min_length=1)
    api_key: SecretStr | None = Field(default=None, repr=False)
    api_key_env: str = Field(default=OPENAI_API_KEY_ENV, min_length=1)
    llm_max_candidates: int = Field(default=4, ge=1, le=20)
    reviewer_backend: str = Field(default=DEFAULT_REVIEWER_BACKEND, min_length=1)
    use_llm_reviewers: bool = False
    reviewer_model: str = Field(default=DEFAULT_LLM_MODEL, min_length=1)
    reviewer_api_key: SecretStr | None = Field(default=None, repr=False)
    reviewer_api_key_env: str = Field(default=OPENAI_API_KEY_ENV, min_length=1)
    reviewer_max_objections: int = Field(
        default=DEFAULT_REVIEWER_MAX_OBJECTIONS,
        ge=1,
        le=20,
    )
    retrieval_backend: str = Field(default=DEFAULT_RETRIEVAL_BACKEND, min_length=1)
    retrieval_api_key: SecretStr | None = Field(default=None, repr=False)
    retrieval_api_key_env: str = Field(default=OPENALEX_API_KEY_ENV, min_length=1)
    retrieval_limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=100)

    @field_validator("adapter_backend")
    @classmethod
    def normalize_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("adapter_backend must not be empty")
        return normalized

    @field_validator("retrieval_backend")
    @classmethod
    def normalize_retrieval_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("retrieval_backend must not be empty")
        return normalized

    @field_validator("reviewer_backend")
    @classmethod
    def normalize_reviewer_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("reviewer_backend must not be empty")
        return normalized

    @field_validator(
        "llm_model",
        "api_key_env",
        "reviewer_model",
        "reviewer_api_key_env",
        "retrieval_api_key_env",
    )
    @classmethod
    def strip_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("adapter text configuration must not be empty")
        return normalized


def load_adapter_config(
    values: AdapterConfig | Mapping[str, Any] | None = None,
) -> AdapterConfig:
    """Load adapter configuration without environment or API-key side effects."""
    if values is None:
        return AdapterConfig()
    if isinstance(values, AdapterConfig):
        return values
    return AdapterConfig.model_validate(dict(values))


__all__ = ["AdapterConfig", "load_adapter_config"]
