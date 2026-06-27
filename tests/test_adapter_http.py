from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from factori.adapters.errors import AdapterResponseParseError, AdapterTransportError
from factori.adapters.http import (
    parse_json_response,
    read_http_error_body,
    request_json,
    sanitize_http_error_body,
)


@dataclass
class FakeResponse:
    body: bytes

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_request_json_uses_injected_opener_without_network() -> None:
    calls: list[dict[str, Any]] = []

    def opener(request: Request, timeout: float) -> FakeResponse:
        calls.append({"url": request.full_url, "timeout": timeout})
        return FakeResponse(b'{"ok": true}')

    result = request_json(
        "https://example.test/path",
        backend="test",
        provider="test",
        operation="demo",
        opener=opener,
    )

    assert result == {"ok": True}
    assert calls == [{"url": "https://example.test/path", "timeout": 60.0}]


def test_request_json_converts_http_error_to_transport_error() -> None:
    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        raise HTTPError(
            "https://example.test/path?api_key=secret",
            500,
            "server",
            hdrs=None,
            fp=BytesIO(b'{"error":{"message":"server failed"}}'),
        )

    with pytest.raises(AdapterTransportError) as excinfo:
        request_json(
            "https://example.test/path?api_key=secret",
            backend="test",
            provider="provider",
            operation="demo",
            opener=opener,
        )

    assert excinfo.value.status_code == 500
    assert excinfo.value.response_body_excerpt == '{"error":{"message":"server failed"}}'
    assert "body=" in str(excinfo.value)
    assert "secret" not in str(excinfo.value)


def test_request_json_captures_http_400_body_for_diagnostics() -> None:
    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        raise HTTPError(
            "https://api.openai.com/v1/responses",
            400,
            "bad request",
            hdrs=None,
            fp=BytesIO(
                b'{"error":{"message":"Unsupported parameter: text.format"}}'
            ),
        )

    with pytest.raises(AdapterTransportError) as excinfo:
        request_json(
            "https://api.openai.com/v1/responses",
            method="POST",
            backend="openai",
            provider="openai",
            operation="responses.create",
            opener=opener,
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.response_body_excerpt is not None
    assert "Unsupported parameter" in str(excinfo.value)
    assert "responses.create" in str(excinfo.value)


def test_read_http_error_body_truncates() -> None:
    error = HTTPError(
        "https://example.test/path",
        400,
        "bad request",
        hdrs=None,
        fp=BytesIO(b"x" * 20),
    )

    assert read_http_error_body(error, max_chars=5) == "xxxxx...[truncated]"


def test_sanitize_http_error_body_redacts_secrets() -> None:
    sanitized = sanitize_http_error_body(
        '{"api_key":"sk-live-secret","Authorization":"Bearer token-secret",'
        '"message":"bad sk-another-secret"}'
    )

    assert "sk-live-secret" not in sanitized
    assert "token-secret" not in sanitized
    assert "sk-another-secret" not in sanitized
    assert "REDACTED" in sanitized


def test_request_json_converts_timeout_to_transport_error() -> None:
    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        raise TimeoutError("timed out")

    with pytest.raises(AdapterTransportError, match="request failed"):
        request_json(
            "https://example.test/path",
            backend="test",
            provider="provider",
            operation="demo",
            opener=opener,
        )


def test_request_json_converts_url_error_to_transport_error() -> None:
    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        raise URLError(TimeoutError("timed out"))

    with pytest.raises(AdapterTransportError):
        request_json(
            "https://example.test/path",
            backend="test",
            provider="provider",
            operation="demo",
            opener=opener,
        )


def test_parse_json_response_rejects_malformed_json() -> None:
    with pytest.raises(AdapterResponseParseError, match="not valid JSON"):
        parse_json_response(
            b"{not-json",
            backend="test",
            provider="provider",
            operation="demo",
        )
