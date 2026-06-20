"""Provider-isolated, explicitly gated OpenAI adapter for Stage A proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.adapters.llm_safety import (
    LLMCandidateResponseError,
    parse_llm_candidate_response_with_report,
)
from factori.schemas import (
    Candidate,
    ConstraintSet,
    LLMCandidateParseReport,
    LLMGenerationTrace,
    ReviewReport,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


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

    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> Any:
        payload = {
            "model": model,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "factori_stage_a_candidates",
                    "strict": True,
                    "schema": response_schema,
                }
            },
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "factori/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"OpenAI Responses request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(
                "OpenAI Responses request failed before a response was received"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI Responses endpoint returned invalid JSON") from exc
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
    is_fake: bool = field(default=False, init=False)
    generation_traces: list[LLMGenerationTrace] = field(default_factory=list, init=False)

    @property
    def external_calls_enabled(self) -> bool:
        """Expose the explicit gate through the shared adapter metadata protocol."""
        return self.allow_external_calls

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise ValueError(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM adapters."
            )
        if not self.api_key.strip():
            raise ValueError("Real LLM adapter requested but no API key is configured.")
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
        raise RuntimeError("OpenAI Responses payload is not a JSON object")
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
    raise RuntimeError("OpenAI Responses payload did not contain structured output text")


def _json_compatible(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("LLM transport returned a non-JSON-compatible response") from exc


__all__ = [
    "LLMTransport",
    "OPENAI_RESPONSES_URL",
    "OpenAILLMClient",
    "OpenAIResponsesTransport",
]
