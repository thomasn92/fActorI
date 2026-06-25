"""Minimal shared HTTP/JSON utilities for gated real adapters."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from factori.adapters.errors import (
    AdapterResponseParseError,
    AdapterTransportError,
    redact_url,
)


class URLOpener(Protocol):
    """Tiny opener protocol used to inject fake transports in tests."""

    def __call__(self, request: Request, timeout: float) -> Any: ...


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
        raise AdapterTransportError(
            backend=backend,
            provider=provider,
            operation=operation,
            status_code=exc.code,
            url=url,
            message=f"HTTP {exc.code}",
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


__all__ = ["URLOpener", "parse_json_response", "redact_url", "request_json"]
