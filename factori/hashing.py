"""Deterministic SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def to_jsonable(value: Any) -> Any:
    """Convert Pydantic models and containers into deterministic JSON-compatible values."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): to_jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Return canonical JSON used for hashing ledger and artifact payloads."""
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_bytes(content: bytes) -> str:
    """Return a lowercase SHA-256 hex digest for bytes."""
    return hashlib.sha256(content).hexdigest()


def sha256_text(content: str) -> str:
    """Return a lowercase SHA-256 hex digest for UTF-8 text."""
    return sha256_bytes(content.encode("utf-8"))


def sha256_json(value: Any) -> str:
    """Return the SHA-256 hash of canonical JSON."""
    return sha256_text(canonical_json(value))


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 hash of a file's raw bytes."""
    return sha256_bytes(Path(path).read_bytes())
