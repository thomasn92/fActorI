"""Runtime LLM accounting and paper-tail reservation for targeted studies."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.llm_budget import (
    LLMBudgetExceeded,
    RuntimeLLMBudgetGuard,
    build_call_accounting_record,
)
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactType,
    ControllerActionType,
    LLMBudgetConfig,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCallStatus,
    TargetedStudyConfig,
)
from factori.storage_protocols import Clock, SystemClock

_CALL_RE = re.compile(r"^targeted-llm-call-(\d{4})\.json$")


class _BudgetedClientProxy:
    def __init__(
        self,
        delegate: Any,
        manager: TargetedLLMBudgetManager,
        method_steps: dict[str, str],
    ) -> None:
        self._delegate = delegate
        self._manager = manager
        self._method_steps = method_steps

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._delegate, name)
        step_name = self._method_steps.get(name)
        if step_name is None or not callable(value):
            return value

        def budgeted(*args: Any, **kwargs: Any) -> Any:
            return self._manager.call(
                step_name=step_name,
                client=self._delegate,
                method_name=name,
                operation=lambda: value(*args, **kwargs),
                request_payload={
                    "method": name,
                    "arguments_hash": sha256_json(_jsonable(kwargs)),
                },
            )

        return budgeted


@dataclass
class TargetedLLMBudgetManager:
    """Authorize and persist every targeted LLM call with a reserved paper tail."""

    config: TargetedStudyConfig
    root: Path
    store: ArtifactStore
    ledger: ResearchLedger
    reserve_calls: int = 0
    reserve_questioner_call: bool = False
    clock: Clock = field(default_factory=SystemClock)
    records: list[LLMCallAccountingRecord] = field(init=False, default_factory=list)
    call_accounting_paths: list[str] = field(init=False, default_factory=list)
    _guard: RuntimeLLMBudgetGuard = field(init=False)
    _reserve_released: bool = field(init=False, default=False)
    _questioner_reserve_released: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.records, self.call_accounting_paths = _load_records(
            root=self.root,
            run_id=self.config.run_id,
        )
        self._guard = RuntimeLLMBudgetGuard(
            budget=self._active_budget(release_reserve=False),
            clock=self.clock.now,
        )
        self._guard.usage = _usage_from_attempts(self.records)

    @property
    def usage(self) -> LLMBudgetUsage:
        return self._guard.usage

    @property
    def estimated_cost_per_call(self) -> float:
        return (
            self.config.estimated_input_tokens_per_call
            * self.config.input_cost_per_million_usd
            + self.config.estimated_output_tokens_per_call
            * self.config.output_cost_per_million_usd
        ) / 1_000_000

    def wrap_client(self, client: Any, method_steps: dict[str, str]) -> Any:
        return _BudgetedClientProxy(client, self, method_steps)

    def can_spend_optional(
        self,
        calls: int = 1,
        *,
        quality_repair_calls: int = 0,
    ) -> bool:
        if calls < 0 or quality_repair_calls < 0 or quality_repair_calls > calls:
            return False
        budget = self._guard.budget
        next_calls = self.usage.total_calls + calls
        next_quality_repairs = self.usage.quality_repair_calls + quality_repair_calls
        next_cost = (self.usage.estimated_cost_usd or 0.0) + (
            calls * self.estimated_cost_per_call
        )
        if budget.max_total_calls is not None and next_calls > budget.max_total_calls:
            return False
        if (
            budget.max_quality_repair_calls is not None
            and next_quality_repairs > budget.max_quality_repair_calls
        ):
            return False
        return not (
            budget.max_estimated_cost_usd is not None
            and next_cost > budget.max_estimated_cost_usd
        )

    @property
    def remaining_optional_calls(self) -> int:
        limit = self._guard.budget.max_total_calls
        if limit is None:
            return 1_000_000
        return max(0, limit - self.usage.total_calls)

    def release_paper_tail_reserve(self) -> None:
        if self._reserve_released:
            return
        self._reserve_released = True
        self._guard.budget = self._active_budget(release_reserve=True)

    def release_adaptive_questioner_reserve(self) -> None:
        if self._questioner_reserve_released:
            return
        self._questioner_reserve_released = True
        self._guard.budget = self._active_budget(
            release_reserve=self._reserve_released
        )

    def call(
        self,
        *,
        step_name: str,
        client: Any,
        method_name: str,
        operation: Callable[[], Any],
        request_payload: dict[str, Any],
    ) -> Any:
        backend = str(getattr(client, "backend_name", "llm-openai"))
        model = str(getattr(client, "model", self.config.model))
        started_at = self.clock.now()
        estimated_cost = self.estimated_cost_per_call
        try:
            self._guard.authorize_call(
                step_name=step_name,
                backend=backend,
                provider="openai",
                model=model,
                request_payload=request_payload,
                input_token_estimate=self.config.estimated_input_tokens_per_call,
                output_token_estimate=self.config.estimated_output_tokens_per_call,
                estimated_cost_usd=estimated_cost,
            )
        except LLMBudgetExceeded as exc:
            self._persist_record(exc.record)
            raise

        try:
            response = operation()
        except Exception as exc:
            record = build_call_accounting_record(
                step_name=step_name,
                backend=backend,
                provider="openai",
                model=model,
                request_payload=request_payload,
                response_payload=None,
                started_at=started_at,
                completed_at=self.clock.now(),
                status=LLMCallStatus.FAILED,
                error_type=type(exc).__name__,
                input_token_estimate=self.config.estimated_input_tokens_per_call,
                output_token_estimate=self.config.estimated_output_tokens_per_call,
                estimated_cost_usd=estimated_cost,
                external_call_performed=True,
            )
            self._persist_record(record)
            raise

        record = build_call_accounting_record(
            step_name=step_name,
            backend=backend,
            provider="openai",
            model=model,
            request_payload=request_payload,
            response_payload={
                "method": method_name,
                "response_type": type(response).__name__,
                "response_hash": sha256_json(_jsonable(response)),
            },
            started_at=started_at,
            completed_at=self.clock.now(),
            status=LLMCallStatus.SUCCEEDED,
            input_token_estimate=self.config.estimated_input_tokens_per_call,
            output_token_estimate=self.config.estimated_output_tokens_per_call,
            estimated_cost_usd=estimated_cost,
            external_call_performed=True,
        )
        self._persist_record(record)
        return response

    def _active_budget(self, *, release_reserve: bool) -> LLMBudgetConfig:
        reserved_calls = 0 if release_reserve else self.reserve_calls
        if self.reserve_questioner_call and not self._questioner_reserve_released:
            reserved_calls += 1
        reserved_cost = reserved_calls * self.estimated_cost_per_call
        failed_quality_transports = sum(
            item.step_name == "llm-quality-repair"
            and item.status == LLMCallStatus.FAILED
            and item.error_type == "AdapterTransportError"
            for item in self.records
        )
        return LLMBudgetConfig(
            max_total_calls=max(0, self.config.max_total_calls - reserved_calls),
            max_quality_repair_calls=(
                (1 if self.config.mode == "full" else 0)
                + 2 * self.config.adaptive_evidence.max_code_repair_calls
                + 2 * self.config.adaptive_evidence.max_plan_repair_calls
                + failed_quality_transports
            ),
            max_estimated_cost_usd=max(0.0, self.config.max_cost_usd - reserved_cost),
            fail_on_budget_unknown=True,
        )

    def _persist_record(self, record: LLMCallAccountingRecord) -> None:
        reports = self.root / "runs" / self.config.run_id / "reports"
        number = _next_number(reports)
        artifact_id = f"targeted-llm-call-{number:04d}"
        result = persist_artifacts_with_commit(
            run_id=self.config.run_id,
            store=self.store,
            ledger=self.ledger,
            artifact_specs=[
                ArtifactWriteSpec(
                    artifact_id,
                    ArtifactType.REPORT,
                    record,
                    "json",
                    {
                        "stage": "targeted_llm_call_accounting",
                        "artifact_role": "accounting_context",
                        "contains_secret": False,
                        "is_verification_evidence": False,
                        "creates_scientific_validation": False,
                        "publication_ready": False,
                    },
                )
            ],
            action_type=ControllerActionType.CONTROLLER_ACTION,
            commit_payload={
                "operation": "targeted_llm_call_accounting",
                "step_name": record.step_name,
                "status": record.status.value,
            },
        )
        self.records.append(record)
        self.call_accounting_paths.append(result.artifacts[0].path)


def _load_records(
    *, root: Path, run_id: str
) -> tuple[list[LLMCallAccountingRecord], list[str]]:
    reports = root / "runs" / run_id / "reports"
    if not reports.is_dir():
        return [], []
    records: list[LLMCallAccountingRecord] = []
    paths: list[str] = []
    for path in sorted(item for item in reports.iterdir() if _CALL_RE.match(item.name)):
        records.append(
            LLMCallAccountingRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        )
        paths.append(path.relative_to(root).as_posix())
    return records, paths


def _usage_from_attempts(records: list[LLMCallAccountingRecord]) -> LLMBudgetUsage:
    attempted = [
        item
        for item in records
        if item.external_call_performed
        and item.status in {LLMCallStatus.SUCCEEDED, LLMCallStatus.FAILED}
    ]
    return LLMBudgetUsage(
        total_calls=len(attempted),
        candidate_generation_calls=sum(
            item.step_name == "llm-candidate-generation" for item in attempted
        ),
        review_calls=sum(item.step_name == "llm-stage-b-review" for item in attempted),
        prose_calls=sum(item.step_name == "llm-prose-generation" for item in attempted),
        quality_repair_calls=sum(item.step_name == "llm-quality-repair" for item in attempted),
        total_input_tokens=sum(item.input_token_estimate or 0 for item in attempted),
        total_output_tokens=sum(item.output_token_estimate or 0 for item in attempted),
        estimated_cost_usd=round(
            sum(item.estimated_cost_usd or 0.0 for item in attempted), 6
        ),
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
            if name not in {"api_key", "transport"}
        }
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _next_number(reports: Path) -> int:
    if not reports.is_dir():
        return 1
    values = [
        int(match.group(1))
        for path in reports.iterdir()
        if (match := _CALL_RE.match(path.name))
    ]
    return max(values, default=0) + 1


__all__ = ["TargetedLLMBudgetManager"]
