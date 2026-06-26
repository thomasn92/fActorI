from __future__ import annotations

from dataclasses import dataclass

import pytest

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import (
    AdapterConfigurationError,
    AdapterExternalCallsDisabled,
)
from factori.adapters.experiment_contracts import build_experiment_run_contract
from factori.adapters.experiment_real import (
    ExperimentToolRunResult,
    LocalSyntheticExperimentRunner,
)
from factori.adapters.registry import get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    VerificationLabel,
)
from factori.stage_c import run_stage_c


@dataclass
class RecordingExperimentRunner:
    exit_code: int = 0
    metrics: dict[str, float] | None = None
    calls: int = 0

    def run(self, **kwargs) -> ExperimentToolRunResult:
        self.calls += 1
        assert kwargs["executable"] == "local-runner"
        assert kwargs["timeout_seconds"] >= 1
        metrics = self.metrics or {"delta": 0.08, "lcb_95": 0.02}
        return ExperimentToolRunResult(
            exit_code=self.exit_code,
            stdout='{"metrics":{"delta":0.08,"lcb_95":0.02}}',
            stderr="",
            output_payload={"metrics": metrics, "synthetic_only": True},
            metrics=metrics,
            elapsed_ms=9,
            runner_version="local-test-version",
        )


def test_fake_experiment_backend_remains_default() -> None:
    registry = get_adapter_registry()

    assert registry.config.experiment_backend == "fake"
    assert registry.config.allow_external_tools is False
    assert registry.class_names()["experiment_runner"] == "FakeExperimentRunner"


def test_real_experiment_backend_fails_if_external_tools_disabled() -> None:
    with pytest.raises(
        AdapterExternalCallsDisabled,
        match="External experiment tools are disabled",
    ):
        get_adapter_registry(
            AdapterConfig(
                experiment_backend="local_synthetic",
                allow_external_tools=False,
                experiment_runner="local-runner",
            ),
            experiment_tool_runner=RecordingExperimentRunner(),
        )


def test_real_experiment_backend_fails_if_runner_missing() -> None:
    with pytest.raises(AdapterConfigurationError, match="experiment runner"):
        get_adapter_registry(
            AdapterConfig(
                experiment_backend="local_synthetic",
                allow_external_tools=True,
                experiment_runner="definitely-not-an-experiment-runner",
            )
        )


def test_real_experiment_backend_uses_injected_runner_without_external_binary() -> None:
    runner = RecordingExperimentRunner()
    registry = get_adapter_registry(
        AdapterConfig(
            experiment_backend="local_synthetic",
            allow_external_tools=True,
            experiment_runner="local-runner",
        ),
        experiment_tool_runner=runner,
    )
    adapter = registry.experiment_runner
    candidate = _candidate()
    contract = build_experiment_run_contract(
        candidate,
        backend="local_synthetic",
        runner_name="local-runner",
    )

    adapter_run = adapter.run_contract(contract)

    assert isinstance(adapter, LocalSyntheticExperimentRunner)
    assert runner.calls == 1
    assert adapter_run.result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    assert adapter_run.result.stdout_hash
    assert adapter_run.result.stderr_hash
    assert adapter_run.result.input_spec_hash
    assert adapter_run.result.output_payload_hash
    assert adapter_run.contract_validation.valid is True
    assert adapter_run.result_validation.valid is True


def test_failed_experiment_result_uses_safe_label() -> None:
    adapter = LocalSyntheticExperimentRunner(
        runner_name="local-runner",
        runner=RecordingExperimentRunner(exit_code=1),
        allow_external_tools=True,
    )

    adapter_run = adapter.run_contract(
        build_experiment_run_contract(
            _candidate(),
            backend="local_synthetic",
            runner_name="local-runner",
        )
    )

    assert adapter_run.result.label in {
        VerificationLabel.NEGATIVE_RESULT,
        VerificationLabel.LIMITATION,
        VerificationLabel.UNSUPPORTED,
    }
    assert adapter_run.result.passed is False


def test_stage_c_with_injected_experiment_runner_writes_real_experiment_artifacts(tmp_path) -> None:
    store, ledger = _stage_c_synthetic_fixture(tmp_path)
    adapter = LocalSyntheticExperimentRunner(
        runner_name="local-runner",
        runner=RecordingExperimentRunner(),
        allow_external_tools=True,
    )

    result = run_stage_c(
        run_id="run-1",
        store=store,
        ledger=ledger,
        experiment_runner=adapter,
    )

    assert result.experiment_backend_metadata["backend"] == "local_synthetic"
    labels = [record.label for record in result.verification_records.values()]
    assert labels == [VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED]
    artifacts = [artifact for values in result.artifacts.values() for artifact in values]
    candidate_id = result.stage_c_ready_candidates[0].id
    experiment_artifact_ids = {
        artifact.id for artifact in artifacts if artifact.type == ArtifactType.EXPERIMENT
    }
    assert {
        f"experiment-contract-{candidate_id}",
        f"experiment-input-{candidate_id}",
        f"experiment-trace-{candidate_id}",
        f"experiment-output-{candidate_id}",
        f"experiment-result-{candidate_id}",
        f"experiment-safety-{candidate_id}",
    } <= experiment_artifact_ids
    experiment_evidence = [
        artifact
        for artifact in artifacts
        if artifact.id == f"experiment-result-{candidate_id}"
    ][0]
    assert experiment_evidence.metadata["evidence_role"] == "synthetic_experiment"
    assert experiment_evidence.producing_commit_hash
    assert len(experiment_evidence.content_hash) == 64

    experiment_commits = [
        commit
        for commit in ledger.list_commits("run-1")
        if commit.action_type == ControllerActionType.STAGE_C_SYNTHETIC_EXPERIMENT_RUN
    ]
    assert experiment_commits[-1].payload["experiment_backend"] == "local_synthetic"
    assert len(experiment_commits[-1].artifact_refs) == 6


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-experiment",
        constraints=ConstraintSet(domain="synthetic methods"),
        domain="synthetic methods",
        method="synthetic simulation",
        question="Can controlled simulation improve the declared metric?",
        hypothesis="Synthetic-only behavior improves the declared metric.",
        experiment="Synthetic simulation with deterministic acceptance criteria.",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )


def _stage_c_synthetic_fixture(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    run_id = "run-1"
    store.init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    candidate = _candidate()
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        candidate_id=candidate.id,
        action_type=ControllerActionType.STAGE_B_CHILD_GENERATED,
        payload={"candidate": candidate.model_dump(mode="json")},
    )
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_BUDGET_SELECTED,
        payload={"selected_candidate_ids": [candidate.id]},
    )
    return store, ledger
