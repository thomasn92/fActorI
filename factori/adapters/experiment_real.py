"""Gated local synthetic experiment adapter with injectable execution."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from factori.adapters.errors import AdapterExternalCallsDisabled, AdapterTransportError
from factori.adapters.experiment_contracts import (
    build_experiment_run_contract,
    experiment_input_spec,
)
from factori.adapters.experiment_safety import (
    ExperimentContractValidationResult,
    ExperimentResultValidationResult,
    metrics_satisfy_acceptance,
    validate_experiment_contract,
    validate_experiment_result,
)
from factori.hashing import sha256_json, sha256_text
from factori.schemas import (
    Candidate,
    ExperimentRunContract,
    ExperimentRunResult,
    VerificationLabel,
)


@dataclass(frozen=True)
class ExperimentToolRunResult:
    """Captured output from an injected synthetic experiment runner."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    output_payload: Mapping[str, Any] | None = None
    metrics: Mapping[str, float] | None = None
    elapsed_ms: int = 0
    runner_version: str | None = None


class ExperimentToolRunner(Protocol):
    """Minimal experiment runner seam used by tests and future local tools."""

    def run(
        self,
        *,
        executable: str,
        args: Sequence[str],
        input_spec: Mapping[str, Any],
        timeout_seconds: int,
    ) -> ExperimentToolRunResult: ...


class SubprocessExperimentToolRunner:
    """Local subprocess runner. It is only invoked after explicit external-tool opt-in."""

    def run(
        self,
        *,
        executable: str,
        args: Sequence[str],
        input_spec: Mapping[str, Any],
        timeout_seconds: int,
    ) -> ExperimentToolRunResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [executable, *args],
                input=json.dumps(input_spec, sort_keys=True),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterTransportError(
                backend="local_synthetic",
                provider="local",
                operation="run_synthetic_experiment",
                message=str(exc),
                cause=exc,
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = _parse_json_payload(completed.stdout)
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
        return ExperimentToolRunResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            output_payload=payload,
            metrics={str(key): float(value) for key, value in metrics.items()},
            elapsed_ms=elapsed_ms,
        )


@dataclass(frozen=True)
class ExperimentAdapterRun:
    """Complete adapter output before Stage C persists artifacts."""

    contract: ExperimentRunContract
    result: ExperimentRunResult
    trace: dict[str, Any]
    output_payload: dict[str, Any]
    input_spec: dict[str, Any]
    contract_validation: ExperimentContractValidationResult
    result_validation: ExperimentResultValidationResult


@dataclass(frozen=True)
class LocalSyntheticExperimentRunner:
    """Gated local synthetic experiment adapter disabled in default fake runs."""

    runner_name: str
    runner: ExperimentToolRunner | None = None
    timeout_seconds: int = 10
    replications: int = 5
    allow_external_tools: bool = False
    backend_name: str = "local_synthetic"
    provider_name: str = "local"
    is_fake: bool = False
    external_calls_enabled: bool = False

    def run_synthetic_experiment(
        self,
        candidate: Candidate,
        experiment_spec: Mapping[str, Any],
    ) -> ExperimentRunResult:
        """Protocol-compatible synthetic experiment entry point."""
        contract = build_experiment_run_contract(
            candidate,
            backend=self.backend_name,
            synthetic_data_spec=_optional_mapping(experiment_spec.get("synthetic_data_spec")),
            model_spec=_optional_mapping(experiment_spec.get("model_spec")),
            algorithm_spec=_optional_mapping(experiment_spec.get("algorithm_spec")),
            metrics=tuple(
                str(value)
                for value in experiment_spec.get("metrics", ("delta", "lcb_95"))
            ),
            acceptance_criteria=_optional_mapping(experiment_spec.get("acceptance_criteria")),
            random_seed=_optional_int(experiment_spec.get("random_seed")),
            replications=int(experiment_spec.get("replications", self.replications)),
            timeout_seconds=int(experiment_spec.get("timeout_seconds", self.timeout_seconds)),
            runner_name=self.runner_name,
        )
        return self.run_contract(contract).result

    def run_contract(self, contract: ExperimentRunContract) -> ExperimentAdapterRun:
        """Validate, run the injected tool, and parse a result deterministically."""
        if not self.allow_external_tools:
            raise AdapterExternalCallsDisabled(
                "External experiment tools are disabled. Set allow_external_tools=true to use "
                "real experiment adapters."
            )
        prepared_contract = contract.model_copy(
            update={
                "backend": self.backend_name,
                "runner_name": contract.runner_name or self.runner_name,
                "timeout_seconds": contract.timeout_seconds or self.timeout_seconds,
                "replications": contract.replications or self.replications,
                "allow_external_tools": True,
                "fake_default": False,
                "is_verification_evidence": False,
            }
        )
        contract_validation = validate_experiment_contract(prepared_contract)
        input_spec = experiment_input_spec(prepared_contract)
        if contract_validation.valid:
            run_result = (self.runner or SubprocessExperimentToolRunner()).run(
                executable=self.runner_name,
                args=(),
                input_spec=input_spec,
                timeout_seconds=prepared_contract.timeout_seconds,
            )
        else:
            run_result = ExperimentToolRunResult(
                exit_code=1,
                stderr="; ".join(contract_validation.reasons),
                output_payload={"errors": list(contract_validation.reasons)},
                metrics={},
            )
        output_payload = dict(
            run_result.output_payload or {"metrics": dict(run_result.metrics or {})}
        )
        result = parse_experiment_tool_run(
            contract=prepared_contract,
            run_result=run_result,
            input_spec=input_spec,
        ).model_copy(
            update={
                "raw_trace_artifact_id": f"experiment-trace-{prepared_contract.candidate_id}",
                "safety_report_artifact_id": f"experiment-safety-{prepared_contract.candidate_id}",
            }
        )
        result_validation = validate_experiment_result(result, prepared_contract)
        trace = {
            "backend": self.backend_name,
            "provider": self.provider_name,
            "runner_name": self.runner_name,
            "exit_code": run_result.exit_code,
            "stdout": run_result.stdout,
            "stderr": run_result.stderr,
            "elapsed_ms": run_result.elapsed_ms,
            "runner_version": run_result.runner_version,
            "fake": False,
            "is_verification_evidence": False,
        }
        return ExperimentAdapterRun(
            contract=prepared_contract,
            result=result,
            trace=trace,
            output_payload=output_payload,
            input_spec=input_spec,
            contract_validation=contract_validation,
            result_validation=result_validation,
        )


def parse_experiment_tool_run(
    *,
    contract: ExperimentRunContract,
    run_result: ExperimentToolRunResult,
    input_spec: Mapping[str, Any],
) -> ExperimentRunResult:
    """Convert runner output into a provider-neutral synthetic experiment result."""
    metrics = {str(key): float(value) for key, value in (run_result.metrics or {}).items()}
    output_payload = dict(run_result.output_payload or {"metrics": metrics})
    passed = (
        run_result.exit_code == 0
        and metrics_satisfy_acceptance(metrics, contract.acceptance_criteria)
    )
    label = (
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        if passed
        else VerificationLabel.NEGATIVE_RESULT
        if run_result.exit_code == 0
        else VerificationLabel.UNSUPPORTED
    )
    reason = (
        "local synthetic runner met the declared synthetic acceptance criteria"
        if passed
        else "local synthetic runner did not meet the declared synthetic acceptance criteria"
        if run_result.exit_code == 0
        else "local synthetic runner failed before validated synthetic evidence was produced"
    )
    return ExperimentRunResult(
        candidate_id=contract.candidate_id,
        claim_id=contract.claim_id,
        experiment_id=contract.experiment_id,
        backend=contract.backend,
        provider="local",
        experiment_kind=contract.experiment_kind,
        data_regime=contract.data_regime,
        runner_name=contract.runner_name or "local_synthetic",
        runner_version=run_result.runner_version,
        exit_code=run_result.exit_code,
        stdout_hash=sha256_text(run_result.stdout),
        stderr_hash=sha256_text(run_result.stderr),
        input_spec_hash=sha256_json(dict(input_spec)),
        output_payload_hash=sha256_json(output_payload),
        metrics=metrics,
        acceptance_criteria=contract.acceptance_criteria,
        passed=passed,
        label=label,
        reason=reason,
        elapsed_ms=run_result.elapsed_ms,
    )


def _parse_json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_stdout": text}
    return dict(payload) if isinstance(payload, Mapping) else {"payload": payload}


def _optional_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


__all__ = [
    "ExperimentAdapterRun",
    "ExperimentToolRunResult",
    "ExperimentToolRunner",
    "LocalSyntheticExperimentRunner",
    "SubprocessExperimentToolRunner",
    "parse_experiment_tool_run",
]
