"""Strict configuration for the adapter interface layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, field_validator

from factori.config import DEFAULT_ADAPTER_BACKEND, DEFAULT_ALLOW_EXTERNAL_CALLS
from factori.schemas import StrictModel


class AdapterConfig(StrictModel):
    """Adapter selection with external access disabled by default."""

    adapter_backend: str = Field(default=DEFAULT_ADAPTER_BACKEND, min_length=1)
    allow_external_calls: bool = DEFAULT_ALLOW_EXTERNAL_CALLS

    @field_validator("adapter_backend")
    @classmethod
    def normalize_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("adapter_backend must not be empty")
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
