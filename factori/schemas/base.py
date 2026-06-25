"""Base schema helpers for strict fActorI Pydantic contracts."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, ValidationError

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

class SchemaError(ValueError):
    """Raised when a schema object violates an MVP invariant."""


class StrictModel(BaseModel):
    """Base model with closed fields for reproducible contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def parse_model_json(model_type: type[StrictModel], data: str) -> StrictModel:
    """Deserialize a strict model and keep ValidationError in the public schema module."""
    try:
        return model_type.model_validate_json(data)
    except ValidationError:
        raise

__all__ = [
    "HASH_RE",
    "SchemaError",
    "StrictModel",
    "parse_model_json",
]
