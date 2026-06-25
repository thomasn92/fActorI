from __future__ import annotations

from factori.adapters.errors import (
    AdapterBackendNotFound,
    AdapterCapabilityError,
    AdapterConfigurationError,
    AdapterError,
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
    AdapterSafetyError,
    AdapterTransportError,
    error_payload,
    redact_url,
)


def test_adapter_errors_are_importable_and_typed() -> None:
    assert issubclass(AdapterConfigurationError, AdapterError)
    assert issubclass(AdapterExternalCallsDisabled, AdapterConfigurationError)
    assert issubclass(AdapterMissingCredentials, AdapterConfigurationError)
    assert issubclass(AdapterBackendNotFound, AdapterConfigurationError)
    assert issubclass(AdapterCapabilityError, AdapterConfigurationError)
    assert issubclass(AdapterSafetyError, AdapterError)
    assert issubclass(AdapterResponseParseError, ValueError)


def test_transport_error_is_structured_and_secret_safe() -> None:
    error = AdapterTransportError(
        backend="openalex",
        provider="openalex",
        operation="works.search",
        status_code=403,
        url="https://api.example.test/search?api_key=secret-token&q=graph",
        message="HTTP 403",
    )

    rendered = str(error)
    payload = error_payload(error)

    assert "secret-token" not in rendered
    assert "api_key=REDACTED" in rendered
    assert payload["status_code"] == 403
    assert payload["url"] == "https://api.example.test/search?api_key=REDACTED&q=graph"


def test_response_parse_error_is_structured_and_secret_safe() -> None:
    error = AdapterResponseParseError(
        backend="openai",
        provider="openai",
        operation="responses.create",
        url="https://api.example.test/response?token=secret",
        message="not json",
    )

    rendered = str(error)

    assert "secret" not in rendered
    assert "token=REDACTED" in rendered


def test_redact_url_preserves_non_secret_query_values() -> None:
    assert redact_url("https://example.test/?q=abc&api_key=secret") == (
        "https://example.test/?q=abc&api_key=REDACTED"
    )
