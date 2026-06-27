"""Shared adapter error types with structured, secret-safe messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_KEYS = ("api_key", "apikey", "key", "token", "secret", "password")


def redact_url(value: str | None) -> str | None:
    """Redact common credential-bearing query values from a URL."""
    if value is None:
        return None
    parsed = urlsplit(value)
    if not parsed.query:
        return value
    query_items: list[tuple[str, str]] = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if any(secret_key in lowered for secret_key in _SECRET_QUERY_KEYS):
            query_items.append((key, "REDACTED"))
        else:
            query_items.append((key, item_value))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_items),
            parsed.fragment,
        )
    )


def _clean_message(value: str) -> str:
    return " ".join(value.split())


class AdapterError(Exception):
    """Base class for all adapter failures."""


class AdapterConfigurationError(AdapterError, ValueError):
    """Raised when adapter configuration is unsafe or unavailable."""


class AdapterExternalCallsDisabled(AdapterConfigurationError):
    """Raised before any network call when external calls are not explicitly enabled."""


class AdapterMissingCredentials(AdapterConfigurationError):
    """Raised when a real adapter is requested without required credentials."""


class AdapterBackendNotFound(AdapterConfigurationError):
    """Raised when a backend name is unknown."""


class AdapterCapabilityError(AdapterConfigurationError):
    """Raised when a backend cannot provide the requested capability."""


@dataclass
class AdapterTransportError(AdapterError):
    """Failure while calling an external transport.

    The string form intentionally avoids secrets and redacts URLs before display.
    """

    backend: str
    provider: str
    operation: str
    message: str
    status_code: int | None = None
    url: str | None = None
    response_body_excerpt: str | None = None
    cause: BaseException | None = None

    def __str__(self) -> str:
        parts = [
            "Adapter transport failed",
            f"backend={self.backend}",
            f"provider={self.provider}",
            f"operation={self.operation}",
        ]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        redacted = redact_url(self.url)
        if redacted is not None:
            parts.append(f"url={redacted}")
        cleaned_message = _clean_message(self.message)
        parts.append(f"message={cleaned_message}")
        if self.response_body_excerpt and "body=" not in cleaned_message:
            parts.append(f"body={_clean_message(self.response_body_excerpt)}")
        return "; ".join(parts)


@dataclass
class AdapterResponseParseError(AdapterError, ValueError):
    """Raised when a backend response is not valid JSON or expected structure."""

    backend: str
    provider: str
    operation: str
    message: str
    url: str | None = None
    cause: BaseException | None = None

    def __str__(self) -> str:
        parts = [
            "Adapter response parse failed",
            f"backend={self.backend}",
            f"provider={self.provider}",
            f"operation={self.operation}",
        ]
        redacted = redact_url(self.url)
        if redacted is not None:
            parts.append(f"url={redacted}")
        parts.append(f"message={_clean_message(self.message)}")
        return "; ".join(parts)


class AdapterSafetyError(AdapterError, ValueError):
    """Raised when adapter output violates local safety/evidence boundaries."""


def error_payload(error: AdapterError) -> dict[str, Any]:
    """Return deterministic structured metadata for tests and future protocols."""
    if isinstance(error, AdapterTransportError):
        return {
            "error_type": type(error).__name__,
            "backend": error.backend,
            "provider": error.provider,
            "operation": error.operation,
            "status_code": error.status_code,
            "url": redact_url(error.url),
            "message": _clean_message(error.message),
            "response_body_excerpt": (
                _clean_message(error.response_body_excerpt)
                if error.response_body_excerpt
                else None
            ),
        }
    if isinstance(error, AdapterResponseParseError):
        return {
            "error_type": type(error).__name__,
            "backend": error.backend,
            "provider": error.provider,
            "operation": error.operation,
            "url": redact_url(error.url),
            "message": _clean_message(error.message),
        }
    return {"error_type": type(error).__name__, "message": str(error)}


__all__ = [
    "AdapterBackendNotFound",
    "AdapterCapabilityError",
    "AdapterConfigurationError",
    "AdapterError",
    "AdapterExternalCallsDisabled",
    "AdapterMissingCredentials",
    "AdapterResponseParseError",
    "AdapterSafetyError",
    "AdapterTransportError",
    "error_payload",
    "redact_url",
]
