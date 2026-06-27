from __future__ import annotations

from factori.llm_budget import (
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
