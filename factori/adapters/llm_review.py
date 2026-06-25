"""Gated OpenAI reviewer adapter limited to Stage B structural critique."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.adapters.reviewer_prompts import build_stage_b_reviewer_prompt
from factori.adapters.reviewer_safety import (
    LLMReviewerResponseError,
    parse_llm_reviewer_response,
    safe_failure_report,
)
from factori.reviewers import resolve_disagreement, run_reviewer_panel
from factori.schemas import Candidate, LLMReviewerParseResult, LLMReviewerTrace, ReviewerPanelResult


@dataclass(frozen=True)
class FakeReviewerClient:
    """Adapter-shaped wrapper around the existing deterministic fake panel."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
        retrieval_context: Mapping[str, Any] | None = None,
    ) -> ReviewerPanelResult:
        del rubric, retrieval_context
        return run_reviewer_panel(candidate)


@dataclass
class OpenAIReviewerClient:
    """Real-but-gated reviewer client with no verification authority."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    max_objections: int = 5
    allow_external_calls: bool = False
    backend_name: str = field(default="openai", init=False)
    provider_name: str = field(default="openai", init=False)
    is_fake: bool = field(default=False, init=False)
    review_traces: list[LLMReviewerTrace] = field(default_factory=list, init=False)

    @property
    def external_calls_enabled(self) -> bool:
        return self.allow_external_calls

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "LLM reviewer adapters."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "Real LLM reviewer adapter requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("Real LLM reviewer adapter requires a non-empty model name.")
        if self.max_objections < 1:
            raise ValueError("max_objections must be at least 1")

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
        retrieval_context: Mapping[str, Any] | None = None,
    ) -> ReviewerPanelResult:
        contract = build_stage_b_reviewer_prompt(
            candidate,
            rubric,
            retrieval_context,
            self.max_objections,
        )
        raw_response = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=contract.prompt_text,
            response_schema=contract.requested_output_schema,
        )
        sanitized_response = _json_compatible(raw_response)
        try:
            parsed = parse_llm_reviewer_response(
                sanitized_response,
                expected_candidate_id=candidate.id,
                data_requirement=candidate.data_requirement,
                max_objections=self.max_objections,
                backend=self.backend_name,
                provider=self.provider_name,
            )
        except LLMReviewerResponseError as exc:
            parsed = LLMReviewerParseResult(
                rejected_reports=[{"index": -1, "reasons": [str(exc)]}],
                fallback_used=True,
                reasons=[str(exc)],
            )
        reports = list(parsed.reports)
        if len(reports) < 3:
            fallback_reasons = [
                *parsed.reasons,
                *[
                    reason
                    for rejected in parsed.rejected_reports
                    for reason in rejected.get("reasons", [])
                ],
            ]
            for index in range(len(reports) + 1, 4):
                reports.append(
                    safe_failure_report(
                        candidate.id,
                        index,
                        fallback_reasons,
                        backend=self.backend_name,
                        provider=self.provider_name,
                    )
                )
            parsed = parsed.model_copy(
                update={"reports": reports, "fallback_used": True}
            )
        self.review_traces.append(
            LLMReviewerTrace(
                request={
                    "backend": self.backend_name,
                    "model": self.model,
                    "prompt_contract": contract.model_dump(mode="json"),
                    "external_calls_enabled": True,
                    "api_key_recorded": False,
                    "reviewer_has_verification_authority": False,
                },
                raw_response=sanitized_response,
                parse_result=parsed,
            )
        )
        return resolve_disagreement(candidate.id, reports)


def _json_compatible(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            message="LLM reviewer transport returned non-JSON-compatible data",
            cause=exc,
        ) from exc


__all__ = ["FakeReviewerClient", "OpenAIReviewerClient"]
