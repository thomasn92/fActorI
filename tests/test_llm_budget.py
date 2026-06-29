from __future__ import annotations

import pytest

from factori.llm_budget import (
    LLMBudgetExceeded,
    RuntimeLLMBudgetGuard,
    budget_is_explicit,
    build_call_accounting_record,
    build_planned_llm_usage,
    evaluate_llm_budget,
)
from factori.schemas import (
    LLMBudgetConfig,
    LLMBudgetDecisionStatus,
    LLMBudgetUsage,
    LLMCallStatus,
)


def test_budget_models_are_importable() -> None:
    assert LLMBudgetConfig
    assert LLMBudgetUsage


def test_budget_requires_explicit_limit_when_requested() -> None:
    decision = evaluate_llm_budget(
        LLMBudgetConfig(),
        build_planned_llm_usage(
            candidate_backend="openai",
            reviewer_backend="fake",
            prose_backend="fake",
            input_tokens=1000,
            output_tokens=500,
            estimated_cost_usd=0.01,
        ),
        require_explicit_budget=True,
    )

    assert decision.allowed is False
    assert decision.decision_status == LLMBudgetDecisionStatus.BLOCKED
    assert "Explicit LLM budget" in decision.reasons[0]


def test_budget_permits_run_within_call_limit() -> None:
    usage = build_planned_llm_usage(
        candidate_backend="openai",
        reviewer_backend="openai",
        prose_backend="fake",
        input_tokens=2000,
        output_tokens=1000,
        estimated_cost_usd=0.02,
    )

    decision = evaluate_llm_budget(
        LLMBudgetConfig(max_total_calls=2, max_estimated_cost_usd=1.0),
        usage,
        require_explicit_budget=True,
    )

    assert budget_is_explicit(decision.budget_config)
    assert decision.allowed is True
    assert usage.total_calls == 2


def test_budget_blocks_run_over_call_limit() -> None:
    usage = build_planned_llm_usage(
        candidate_backend="openai",
        reviewer_backend="openai",
        prose_backend="openai",
        input_tokens=3000,
        output_tokens=1500,
        estimated_cost_usd=0.03,
    )

    decision = evaluate_llm_budget(
        LLMBudgetConfig(max_total_calls=2, max_estimated_cost_usd=1.0),
        usage,
        require_explicit_budget=True,
    )

    assert decision.allowed is False
    assert "max_total_calls exceeded" in "; ".join(decision.reasons)


def test_budget_blocks_run_over_estimated_cost() -> None:
    usage = build_planned_llm_usage(
        candidate_backend="openai",
        reviewer_backend="fake",
        prose_backend="fake",
        input_tokens=1000,
        output_tokens=500,
        estimated_cost_usd=2.0,
    )

    decision = evaluate_llm_budget(
        LLMBudgetConfig(max_total_calls=1, max_estimated_cost_usd=1.0),
        usage,
        require_explicit_budget=True,
    )

    assert decision.allowed is False
    assert "estimated cost exceeds" in "; ".join(decision.reasons)


def test_unknown_cost_blocks_or_warns_by_config() -> None:
    usage = build_planned_llm_usage(
        candidate_backend="openai",
        reviewer_backend="fake",
        prose_backend="fake",
    )
    blocked = evaluate_llm_budget(
        LLMBudgetConfig(max_total_calls=1, fail_on_budget_unknown=True),
        usage,
        require_explicit_budget=True,
    )
    warning = evaluate_llm_budget(
        LLMBudgetConfig(max_total_calls=1, fail_on_budget_unknown=False),
        usage,
        require_explicit_budget=True,
    )

    assert blocked.allowed is False
    assert any("estimated cost is unknown" in reason for reason in blocked.reasons)
    assert warning.allowed is True
    assert warning.decision_status == LLMBudgetDecisionStatus.ALLOWED_WITH_WARNINGS


def test_rate_limit_metadata_is_recorded() -> None:
    usage = build_planned_llm_usage(
        candidate_backend="openai",
        reviewer_backend="fake",
        prose_backend="fake",
        input_tokens=1000,
        output_tokens=500,
        estimated_cost_usd=0.01,
        rate_limit_per_minute=3,
    )

    decision = evaluate_llm_budget(
        LLMBudgetConfig(
            max_total_calls=1,
            max_estimated_cost_usd=1.0,
            rate_limit_per_minute=3,
        ),
        usage,
        require_explicit_budget=True,
    )

    assert decision.allowed is True
    assert decision.rate_limit_per_minute == 3
    assert any("rate_limit_per_minute=3" in warning for warning in decision.warnings)


def test_call_accounting_record_is_deterministic_and_secret_safe() -> None:
    kwargs = dict(
        step_name="llm-candidate-generation",
        backend="openai",
        provider="openai",
        model="test-model",
        request_payload={"api_key": "secret", "prompt": "hello"},
        response_payload={"output": "world"},
        started_at="1970-01-01T00:00:00.000000Z",
        completed_at="1970-01-01T00:00:01.000000Z",
        status=LLMCallStatus.SUCCEEDED,
        external_call_performed=True,
    )

    first = build_call_accounting_record(**kwargs)
    second = build_call_accounting_record(**kwargs)

    assert first == second
    assert first.contains_secret is False
    assert first.request_hash != first.response_hash
    assert "api_key" not in first.model_dump_json()


def test_runtime_budget_guard_allows_call_within_total_limit() -> None:
    guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_total_calls=1, max_estimated_cost_usd=0.2),
        lambda: "1970-01-01T00:00:00.000000Z",
    )

    decision = guard.authorize_call(
        step_name="llm-candidate-generation",
        backend="openai",
        provider="openai",
        model="test-model",
        request_payload={"prompt_hash": "abc"},
        input_token_estimate=10,
        output_token_estimate=5,
        estimated_cost_usd=0.01,
    )

    assert decision.allowed is True
    assert guard.usage.total_calls == 1
    assert guard.usage.candidate_generation_calls == 1
    assert guard.blocked_records == []


def test_runtime_budget_guard_blocks_total_call_limit_before_external_call() -> None:
    guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_total_calls=1, max_estimated_cost_usd=0.2),
        lambda: "1970-01-01T00:00:00.000000Z",
    )
    _authorize_candidate(guard)

    with pytest.raises(LLMBudgetExceeded, match="max_total_calls exceeded"):
        _authorize_candidate(guard)

    assert guard.usage.total_calls == 1
    assert len(guard.blocked_records) == 1
    blocked = guard.blocked_records[0]
    assert blocked.status == LLMCallStatus.BLOCKED
    assert blocked.error_type == "BudgetExceeded"
    assert blocked.external_call_performed is False
    assert blocked.contains_secret is False


def test_runtime_budget_guard_blocks_candidate_review_and_prose_limits() -> None:
    candidate_guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_candidate_generation_calls=0, max_estimated_cost_usd=0.2),
        lambda: "1970-01-01T00:00:00.000000Z",
    )
    review_guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_review_calls=0, max_estimated_cost_usd=0.2),
        lambda: "1970-01-01T00:00:00.000000Z",
    )
    prose_guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_prose_calls=0, max_estimated_cost_usd=0.2),
        lambda: "1970-01-01T00:00:00.000000Z",
    )

    with pytest.raises(LLMBudgetExceeded, match="max_candidate_generation_calls"):
        _authorize_candidate(candidate_guard)
    with pytest.raises(LLMBudgetExceeded, match="max_review_calls"):
        _authorize_step(review_guard, "llm-stage-b-review")
    with pytest.raises(LLMBudgetExceeded, match="max_prose_calls"):
        _authorize_step(prose_guard, "llm-prose-generation")


def test_runtime_budget_guard_blocks_unknown_usage_when_fail_closed() -> None:
    guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_total_calls=1, fail_on_budget_unknown=True),
        lambda: "1970-01-01T00:00:00.000000Z",
    )

    with pytest.raises(LLMBudgetExceeded, match="runtime token usage is unknown"):
        guard.authorize_call(
            step_name="llm-candidate-generation",
            backend="openai",
            provider="openai",
            model="test-model",
            request_payload={"prompt_hash": "abc"},
            input_token_estimate=None,
            output_token_estimate=None,
            estimated_cost_usd=None,
        )

    assert guard.blocked_records[0].external_call_performed is False


def test_runtime_budget_guard_warns_and_allows_unknown_usage_when_configured() -> None:
    guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(max_total_calls=1, fail_on_budget_unknown=False),
        lambda: "1970-01-01T00:00:00.000000Z",
    )

    decision = guard.authorize_call(
        step_name="llm-candidate-generation",
        backend="openai",
        provider="openai",
        model="test-model",
        request_payload={"prompt_hash": "abc"},
        input_token_estimate=None,
        output_token_estimate=None,
        estimated_cost_usd=None,
    )

    assert decision.allowed is True
    assert any("runtime token usage is unknown" in warning for warning in decision.warnings)
    assert any("runtime estimated cost is unknown" in warning for warning in decision.warnings)


def test_runtime_guard_enforces_claim_adjudication_call_limit() -> None:
    guard = RuntimeLLMBudgetGuard(
        LLMBudgetConfig(
            max_total_calls=2,
            max_claim_adjudication_calls=1,
            max_estimated_cost_usd=1.0,
        ),
        lambda: "1970-01-01T00:00:00.000000Z",
    )

    _authorize_step(guard, "llm-claim-adjudication")
    with pytest.raises(LLMBudgetExceeded, match="max_claim_adjudication_calls") as error:
        _authorize_step(guard, "llm-claim-adjudication")

    assert error.value.record.external_call_performed is False
    assert error.value.record.error_type == "BudgetExceeded"


def _authorize_candidate(guard: RuntimeLLMBudgetGuard) -> None:
    _authorize_step(guard, "llm-candidate-generation")


def _authorize_step(guard: RuntimeLLMBudgetGuard, step_name: str) -> None:
    guard.authorize_call(
        step_name=step_name,
        backend="openai",
        provider="openai",
        model="test-model",
        request_payload={"prompt_hash": "abc", "api_key": "secret"},
        input_token_estimate=10,
        output_token_estimate=5,
        estimated_cost_usd=0.01,
    )
