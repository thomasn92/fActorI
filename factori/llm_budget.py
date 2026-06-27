"""Deterministic LLM budget and accounting helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from factori.hashing import sha256_json
from factori.schemas import (
    LLMBudgetConfig,
    LLMBudgetDecision,
    LLMBudgetDecisionStatus,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCallStatus,
)

_FAKE_BACKENDS = {"fake"}


class LLMBudgetError(RuntimeError):
    """Raised when a requested LLM orchestration budget is unsafe or exhausted."""


def budget_is_explicit(budget: LLMBudgetConfig) -> bool:
    """Return whether any meaningful real-run LLM budget limit was configured."""
    return any(
        value is not None
        for value in (
            budget.max_total_calls,
            budget.max_candidate_generation_calls,
            budget.max_review_calls,
            budget.max_prose_calls,
            budget.max_total_input_tokens,
            budget.max_total_output_tokens,
            budget.max_estimated_cost_usd,
            budget.max_wallclock_seconds,
            budget.rate_limit_per_minute,
        )
    )


def build_planned_llm_usage(
    *,
    candidate_backend: str,
    reviewer_backend: str,
    prose_backend: str,
    candidate_generation_calls: int = 1,
    review_calls: int = 1,
    prose_calls: int = 1,
    estimated_cost_usd: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    rate_limit_per_minute: int | None = None,
) -> LLMBudgetUsage:
    """Build conservative planned usage for enabled real LLM categories."""
    candidate_calls = (
        max(0, candidate_generation_calls)
        if _is_real_backend(candidate_backend)
        else 0
    )
    reviewer_calls = max(0, review_calls) if _is_real_backend(reviewer_backend) else 0
    section_calls = max(0, prose_calls) if _is_real_backend(prose_backend) else 0
    total_calls = candidate_calls + reviewer_calls + section_calls
    return LLMBudgetUsage(
        total_calls=total_calls,
        candidate_generation_calls=candidate_calls,
        review_calls=reviewer_calls,
        prose_calls=section_calls,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        unknown_token_usage=input_tokens is None or output_tokens is None,
        unknown_cost=estimated_cost_usd is None and total_calls > 0,
        rate_limit_per_minute=rate_limit_per_minute,
    )


def evaluate_llm_budget(
    budget: LLMBudgetConfig,
    planned_usage: LLMBudgetUsage,
    *,
    require_explicit_budget: bool = False,
) -> LLMBudgetDecision:
    """Evaluate planned LLM usage against explicit fail-closed budget limits."""
    reasons: list[str] = []
    warnings: list[str] = []
    if require_explicit_budget and not budget_is_explicit(budget):
        reasons.append("Explicit LLM budget is required for real LLM orchestration.")

    _check_limit(
        reasons,
        "max_total_calls",
        planned_usage.total_calls,
        budget.max_total_calls,
    )
    _check_limit(
        reasons,
        "max_candidate_generation_calls",
        planned_usage.candidate_generation_calls,
        budget.max_candidate_generation_calls,
    )
    _check_limit(
        reasons,
        "max_review_calls",
        planned_usage.review_calls,
        budget.max_review_calls,
    )
    _check_limit(
        reasons,
        "max_prose_calls",
        planned_usage.prose_calls,
        budget.max_prose_calls,
    )
    if planned_usage.total_calls > 0 and planned_usage.total_input_tokens is None:
        _unknown_budget_item(reasons, warnings, budget, "input token usage")
    elif planned_usage.total_input_tokens is not None:
        _check_limit(
            reasons,
            "max_total_input_tokens",
            planned_usage.total_input_tokens,
            budget.max_total_input_tokens,
        )
    if planned_usage.total_calls > 0 and planned_usage.total_output_tokens is None:
        _unknown_budget_item(reasons, warnings, budget, "output token usage")
    elif planned_usage.total_output_tokens is not None:
        _check_limit(
            reasons,
            "max_total_output_tokens",
            planned_usage.total_output_tokens,
            budget.max_total_output_tokens,
        )
    if planned_usage.total_calls > 0 and planned_usage.estimated_cost_usd is None:
        _unknown_budget_item(reasons, warnings, budget, "estimated cost")
    elif (
        budget.max_estimated_cost_usd is not None
        and planned_usage.estimated_cost_usd > budget.max_estimated_cost_usd
    ):
        reasons.append(
            "estimated cost exceeds max_estimated_cost_usd: "
            f"{planned_usage.estimated_cost_usd:.6f} > {budget.max_estimated_cost_usd:.6f}"
        )

    if budget.rate_limit_per_minute is not None and planned_usage.total_calls:
        warnings.append(
            f"rate_limit_per_minute={budget.rate_limit_per_minute} will be enforced by "
            "orchestration metadata only; complex retries are not enabled."
        )
    if reasons:
        status = LLMBudgetDecisionStatus.BLOCKED
    elif warnings:
        status = LLMBudgetDecisionStatus.ALLOWED_WITH_WARNINGS
    else:
        status = LLMBudgetDecisionStatus.ALLOWED
    return LLMBudgetDecision(
        decision_status=status,
        allowed=status != LLMBudgetDecisionStatus.BLOCKED,
        budget_config=budget,
        planned_usage=planned_usage,
        reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
        rate_limit_per_minute=budget.rate_limit_per_minute,
    )


def observed_usage_from_records(
    records: list[LLMCallAccountingRecord],
) -> LLMBudgetUsage:
    """Aggregate observed secret-safe call records into usage totals."""
    successful = [record for record in records if record.status == LLMCallStatus.SUCCEEDED]
    candidate_calls = sum(
        1 for record in successful if record.step_name == "llm-candidate-generation"
    )
    review_calls = sum(
        1 for record in successful if record.step_name == "llm-stage-b-review"
    )
    prose_calls = sum(
        1 for record in successful if record.step_name == "llm-prose-generation"
    )
    input_tokens = _sum_optional(record.input_token_estimate for record in successful)
    output_tokens = _sum_optional(record.output_token_estimate for record in successful)
    cost = _sum_optional_float(record.estimated_cost_usd for record in successful)
    return LLMBudgetUsage(
        total_calls=len(successful),
        candidate_generation_calls=candidate_calls,
        review_calls=review_calls,
        prose_calls=prose_calls,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        estimated_cost_usd=cost,
        unknown_token_usage=input_tokens is None or output_tokens is None,
        unknown_cost=cost is None and bool(successful),
    )


def build_call_accounting_record(
    *,
    step_name: str,
    backend: str,
    provider: str,
    model: str | None,
    request_payload: Mapping[str, Any],
    response_payload: Any | None,
    started_at: str,
    completed_at: str,
    status: LLMCallStatus,
    error_type: str | None = None,
    input_token_estimate: int | None = None,
    output_token_estimate: int | None = None,
    estimated_cost_usd: float | None = None,
    retry_status: str = "retry_not_enabled",
    external_call_performed: bool = False,
) -> LLMCallAccountingRecord:
    """Create one deterministic accounting record without storing raw secrets."""
    sanitized_request = _redact_mapping(request_payload)
    sanitized_response = _redact_payload(response_payload)
    return LLMCallAccountingRecord(
        step_name=step_name,
        backend=backend,
        provider=provider,
        model=model,
        request_hash=sha256_json(sanitized_request),
        response_hash=(
            sha256_json(sanitized_response) if sanitized_response is not None else None
        ),
        input_token_estimate=input_token_estimate,
        output_token_estimate=output_token_estimate,
        estimated_cost_usd=estimated_cost_usd,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        error_type=error_type,
        retry_status=retry_status,
        external_call_performed=external_call_performed,
        contains_secret=False,
    )


def _is_real_backend(value: str) -> bool:
    return value.strip().lower() not in _FAKE_BACKENDS


def _check_limit(
    reasons: list[str],
    name: str,
    observed: int,
    limit: int | None,
) -> None:
    if limit is not None and observed > limit:
        reasons.append(f"{name} exceeded: {observed} > {limit}")


def _unknown_budget_item(
    reasons: list[str],
    warnings: list[str],
    budget: LLMBudgetConfig,
    label: str,
) -> None:
    if budget.fail_on_budget_unknown:
        reasons.append(f"{label} is unknown and fail_on_budget_unknown=true")
    else:
        warnings.append(f"{label} is unknown")


def _sum_optional(values) -> int | None:
    items = list(values)
    if any(value is None for value in items):
        return None
    return sum(int(value) for value in items)


def _sum_optional_float(values) -> float | None:
    items = list(values)
    if any(value is None for value in items):
        return None
    return round(sum(float(value) for value in items), 6)


def _redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _redact_payload(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            lowered = str(key).lower()
            if any(secret in lowered for secret in ("api_key", "token", "secret", "password")):
                redacted[str(key)] = "REDACTED"
            else:
                redacted[str(key)] = _redact_payload(item)
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_payload(item) for item in value]
    return value


__all__ = [
    "LLMBudgetError",
    "budget_is_explicit",
    "build_call_accounting_record",
    "build_planned_llm_usage",
    "evaluate_llm_budget",
    "observed_usage_from_records",
]
