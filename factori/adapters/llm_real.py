"""Provider-isolated, explicitly gated OpenAI adapter for Stage A proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
    AdapterTransportError,
)
from factori.adapters.http import URLOpener, request_json
from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.adapters.llm_safety import (
    LLMCandidateResponseError,
    parse_llm_candidate_response_with_report,
)
from factori.adapters.openai_schema import make_openai_strict_json_schema
from factori.hashing import sha256_json
from factori.schemas import (
    Candidate,
    ConstraintSet,
    LLMCandidateParseReport,
    LLMGenerationTrace,
    ReviewReport,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_REASONING_EFFORTS = frozenset({"low", "medium", "high"})


class LLMTransport(Protocol):
    """Small injectable transport used to keep network calls out of tests."""

    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True)
class OpenAIResponsesTransport:
    """Minimal standard-library transport for the OpenAI Responses endpoint."""

    endpoint: str = OPENAI_RESPONSES_URL
    timeout_seconds: float = 60.0
    opener: URLOpener | None = None
    schema_name: str = "factori_stage_a_candidates"
    nullable_optional_fields: bool = True
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in OPENAI_REASONING_EFFORTS
        ):
            raise ValueError("reasoning_effort must be low, medium, high, or None")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive or None")

    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> Any:
        schema_name = self.schema_name
        payload = _responses_payload(
            model,
            prompt,
            response_schema,
            schema_name,
            nullable_optional_fields=self.nullable_optional_fields,
            reasoning_effort=self.reasoning_effort,
            max_output_tokens=self.max_output_tokens,
        )
        try:
            body = request_json(
                self.endpoint,
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "factori/0.1",
                },
                payload=payload,
                timeout_seconds=self.timeout_seconds,
                backend="openai",
                provider="openai",
                operation="responses.create",
                opener=self.opener,
            )
        except AdapterTransportError as exc:
            _annotate_openai_transport_error(
                exc,
                build_openai_request_diagnostics(
                    model=model,
                    prompt=prompt,
                    response_schema=response_schema,
                    endpoint=self.endpoint,
                    schema_name=schema_name,
                    nullable_optional_fields=self.nullable_optional_fields,
                    reasoning_effort=self.reasoning_effort,
                    max_output_tokens=self.max_output_tokens,
                ),
            )
            raise
        return _extract_output_text(body)


@dataclass
class OpenAILLMClient:
    """Real-but-gated LLM client restricted to Stage A candidate proposals."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    max_candidates: int = 4
    allow_external_calls: bool = False
    backend_name: str = field(default="openai", init=False)
    provider_name: str = field(default="openai", init=False)
    is_fake: bool = field(default=False, init=False)
    generation_traces: list[LLMGenerationTrace] = field(default_factory=list, init=False)

    @property
    def external_calls_enabled(self) -> bool:
        """Expose the explicit gate through the shared adapter metadata protocol."""
        return self.allow_external_calls

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM adapters."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "Real LLM adapter requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("Real LLM adapter requires a non-empty model name.")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")

    def generate_candidates(
        self,
        prompt: str,
        constraints: ConstraintSet,
    ) -> list[Candidate]:
        """Request structured candidate ideas, then validate them locally."""
        del prompt
        contract = build_stage_a_candidate_prompt(
            constraints.domain or "general research",
            constraints.method,
            constraints,
            self.max_candidates,
        )
        raw_response = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=contract.prompt_text,
            response_schema=contract.requested_output_schema,
        )
        sanitized_response = _json_compatible(raw_response)
        try:
            candidates, parse_report = parse_llm_candidate_response_with_report(
                sanitized_response,
                max_candidates=self.max_candidates,
                backend=self.backend_name,
                provider=self.provider_name,
            )
        except LLMCandidateResponseError as exc:
            candidates = []
            parse_report = LLMCandidateParseReport(
                rejected_candidates=[{"index": -1, "reasons": [str(exc)]}],
                max_candidates=self.max_candidates,
            )
        self.generation_traces.append(
            LLMGenerationTrace(
                request={
                    "backend": self.backend_name,
                    "model": self.model,
                    "request_diagnostics": build_openai_request_diagnostics(
                        model=self.model,
                        prompt=contract.prompt_text,
                        response_schema=contract.requested_output_schema,
                        endpoint=getattr(self.transport, "endpoint", OPENAI_RESPONSES_URL),
                    ),
                    "prompt_contract": contract.model_dump(mode="json"),
                    "external_calls_enabled": True,
                    "api_key_recorded": False,
                },
                raw_response=sanitized_response,
                parse_report=parse_report,
            )
        )
        return candidates

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
    ) -> ReviewReport:
        del candidate, rubric
        raise NotImplementedError(
            "The real LLM adapter is limited to Stage A candidate generation."
        )

    def summarize_context(self, context: str | Mapping[str, Any]) -> str:
        del context
        raise NotImplementedError(
            "The real LLM adapter is limited to Stage A candidate generation."
        )


def _extract_output_text(response_body: Any) -> str:
    if not isinstance(response_body, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            message="OpenAI Responses payload is not a JSON object",
        )
    direct = response_body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise AdapterResponseParseError(
        backend="openai",
        provider="openai",
        operation="responses.create",
        message="OpenAI Responses payload did not contain structured output text",
    )


def _responses_payload(
    model: str,
    prompt: str,
    response_schema: dict[str, Any],
    schema_name: str,
    *,
    nullable_optional_fields: bool = True,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    strict_schema = make_openai_strict_json_schema(
        response_schema,
        nullable_optional_fields=nullable_optional_fields,
    )
    payload: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": strict_schema,
            }
        },
    }
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    return payload


def build_openai_request_diagnostics(
    *,
    model: str,
    prompt: str,
    response_schema: dict[str, Any],
    endpoint: str = OPENAI_RESPONSES_URL,
    schema_name: str = "factori_stage_a_candidates",
    nullable_optional_fields: bool = True,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    """Return secret-free metadata for diagnosing Responses request failures."""
    return {
        "endpoint": endpoint,
        "operation": "responses.create",
        "model": model,
        "reasoning_effort": reasoning_effort or "default",
        "max_output_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "name": schema_name,
            "strict": True,
        },
        "prompt_hash": sha256_json({"prompt": prompt}),
        "request_payload_hash": sha256_json(
            _responses_payload(
                model,
                prompt,
                response_schema,
                schema_name,
                nullable_optional_fields=nullable_optional_fields,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
        ),
    }


def _annotate_openai_transport_error(
    error: AdapterTransportError,
    diagnostics: dict[str, Any],
) -> None:
    error.message = (
        f"{error.message}; model={diagnostics['model']}; "
        f"reasoning_effort={diagnostics['reasoning_effort']}; "
        f"response_format={diagnostics['response_format']['type']}; "
        f"prompt_hash={diagnostics['prompt_hash']}; "
        f"request_payload_hash={diagnostics['request_payload_hash']}"
    )


def _json_compatible(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            message="LLM transport returned a non-JSON-compatible response",
            cause=exc,
        ) from exc


__all__ = [
    "LLMTransport",
    "OPENAI_RESPONSES_URL",
    "OpenAILLMClient",
    "OpenAIResponsesTransport",
    "build_openai_request_diagnostics",
]
