"""Minimal shared HTTP/JSON utilities for gated real adapters."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from factori.adapters.errors import (
    AdapterResponseParseError,
    AdapterTransportError,
    redact_url,
)

MAX_HTTP_ERROR_BODY_CHARS = 4000
_SECRET_PATTERNS = (
    (
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]+"),
        r"\1REDACTED",
    ),
    (
        re.compile(r"sk-[A-Za-z0-9._~+/=\-]+"),
        "sk-REDACTED",
    ),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|token|secret|password)"
            r"['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}\]]+"
        ),
        r"\1REDACTED",
    ),
    (
        re.compile(
            r"(?i)((?:authorization)"
            r"['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}\]]+"
        ),
        r"\1REDACTED",
    ),
)


class URLOpener(Protocol):
    """Tiny opener protocol used to inject fake transports in tests."""

    def __call__(self, request: Request, timeout: float) -> Any: ...


def sanitize_http_error_body(body: str) -> str:
    """Return a compact HTTP error body excerpt with common secrets redacted."""
    sanitized = body
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return " ".join(sanitized.split())


def read_http_error_body(
    exc: HTTPError,
    max_chars: int = MAX_HTTP_ERROR_BODY_CHARS,
) -> str:
    """Read and sanitize an HTTPError body without raising secondary errors."""
    try:
        raw_body = exc.read()
    except Exception:  # pragma: no cover - defensive against unusual fp objects
        return ""
    if isinstance(raw_body, bytes):
        text = raw_body.decode("utf-8", errors="replace")
    else:
        text = str(raw_body)
    sanitized = sanitize_http_error_body(text)
    if max_chars < 1:
        return ""
    if len(sanitized) > max_chars:
        return sanitized[:max_chars] + "...[truncated]"
    return sanitized


def parse_json_response(
    body: bytes | str,
    *,
    backend: str,
    provider: str,
    operation: str,
    url: str | None = None,
) -> Any:
    """Parse a JSON response body or raise a typed parse error."""
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterResponseParseError(
            backend=backend,
            provider=provider,
            operation=operation,
            url=url,
            message="response body is not valid JSON",
            cause=exc,
        ) from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 60.0,
    backend: str,
    provider: str,
    operation: str,
    opener: URLOpener | None = None,
) -> Any:
    """Perform one JSON request with deterministic typed error conversion."""
    data = None
    normalized_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        normalized_headers.setdefault("Content-Type", "application/json")
    request = Request(
        url,
        data=data,
        headers=normalized_headers,
        method=method,
    )
    selected_opener = urlopen if opener is None else opener
    try:
        with selected_opener(request, timeout=timeout_seconds) as response:  # noqa: S310
            return parse_json_response(
                response.read(),
                backend=backend,
                provider=provider,
                operation=operation,
                url=url,
            )
    except HTTPError as exc:
        response_body_excerpt = read_http_error_body(exc)
        message = f"HTTP {exc.code}"
        if response_body_excerpt:
            message = f"{message}; body={response_body_excerpt}"
        raise AdapterTransportError(
            backend=backend,
            provider=provider,
            operation=operation,
            status_code=exc.code,
            url=url,
            message=message,
            response_body_excerpt=response_body_excerpt or None,
            cause=exc,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AdapterTransportError(
            backend=backend,
            provider=provider,
            operation=operation,
            url=url,
            message="request failed before a valid response was received",
            cause=exc,
        ) from exc


__all__ = [
    "MAX_HTTP_ERROR_BODY_CHARS",
    "URLOpener",
    "parse_json_response",
    "read_http_error_body",
    "redact_url",
    "request_json",
    "sanitize_http_error_body",
]
