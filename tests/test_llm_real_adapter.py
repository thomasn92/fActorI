from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.llm_real import OpenAILLMClient
from factori.adapters.registry import AdapterConfigurationError, get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schemas import (
    BranchStatus,
    ConstraintSet,
    ControllerActionType,
    PipelineRunConfig,
)
from factori.stage_a import run_stage_a


@dataclass
class StubTransport:
    response: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def test_real_backend_fails_before_transport_when_external_calls_disabled() -> None:
    transport = StubTransport(_response())

    with pytest.raises(AdapterConfigurationError, match="External calls are disabled"):
        get_adapter_registry(
            AdapterConfig(
                adapter_backend="openai",
                allow_external_calls=False,
                api_key="test-key",
            ),
            llm_transport=transport,
        )

    assert transport.calls == []


def test_real_backend_fails_clearly_without_api_key() -> None:
    with pytest.raises(AdapterConfigurationError, match="no API key is configured"):
        get_adapter_registry(
            AdapterConfig(adapter_backend="openai", allow_external_calls=True),
            environ={},
        )


def test_openai_client_itself_defaults_to_external_calls_disabled() -> None:
    with pytest.raises(ValueError, match="External calls are disabled"):
        OpenAILLMClient(
            api_key="test-key",
            model="test-model",
            transport=StubTransport(_response()),
        )


def test_openai_client_uses_injected_transport_without_network() -> None:
    transport = StubTransport(_response())
    client = OpenAILLMClient(
        api_key="test-key",
        model="test-model",
        transport=transport,
        max_candidates=2,
        allow_external_calls=True,
    )

    first = client.generate_candidates(
        "structured prompt",
        ConstraintSet(domain="machine learning", method="calibration"),
    )

    assert len(first) == 2
    assert len(transport.calls) == 1
    assert transport.calls[0]["api_key"] == "test-key"
    assert transport.calls[0]["model"] == "test-model"
    assert client.generation_traces[0].request["api_key_recorded"] is False


def test_stage_a_real_client_writes_ledgered_non_evidence_trace_artifacts(tmp_path) -> None:
    transport = StubTransport(_response())
    registry = get_adapter_registry(
        AdapterConfig(
            adapter_backend="openai",
            allow_external_calls=True,
            api_key="test-key",
            llm_model="test-model",
            llm_max_candidates=4,
        ),
        llm_transport=transport,
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        store=store,
        ledger=ledger,
        llm_client=registry.llm,
    )

    assert len(result.generated_candidates) == 4
    assert result.deferred_candidates[0].status in {
        BranchStatus.DEFERRED_REAL_DATA_CANDIDATE,
        BranchStatus.REQUIRES_REAL_DATA,
    }
    assert set(result.llm_artifacts) == {"request", "response", "parse_report"}
    assert all(ref.content_hash for ref in result.llm_artifacts.values())
    assert all(ref.producing_commit_hash for ref in result.llm_artifacts.values())
    assert all(
        ref.metadata["is_verification_evidence"] is False
        for ref in result.llm_artifacts.values()
    )
    assert all(
        not ref.is_mvp_verification_evidence()
        for ref in result.llm_artifacts.values()
    )
    assert all(candidate.id in result.scores for candidate in result.generated_candidates)
    assert any(
        commit.action_type == ControllerActionType.STAGE_A_LLM_CANDIDATES_PROPOSED
        for commit in ledger.list_commits("run-1")
    )
    report_text = (tmp_path / result.report_artifact.path).read_text(encoding="utf-8")
    assert "Candidate adapter: openai" in report_text
    assert "Candidate adapter model: test-model" in report_text

    manifest = build_artifact_manifest("run-1", store)
    trace_ids = {
        "llm-stage-a-request",
        "llm-stage-a-response",
        "llm-stage-a-parse-report",
    }
    llm_entries = [entry for entry in manifest.artifacts if entry.artifact_id in trace_ids]
    assert len(llm_entries) == 3
    assert all(not entry.is_evidence for entry in llm_entries)
    assert all(entry.is_presentation for entry in llm_entries)


def test_default_stage_a_cli_remains_fake(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "generated_candidates=15" in result.output
    assert "adapter_backend=fake" in result.output
    assert not (tmp_path / "runs" / "run-1" / "reports" / "llm-stage-a-response.json").exists()


def test_real_stage_a_cli_requires_explicit_opt_in_without_mutating_run(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--adapter-backend",
            "openai",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr
    assert not (tmp_path / "runs" / "run-1").exists()


def test_run_all_real_backend_requires_explicit_opt_in_before_mutation(tmp_path) -> None:
    with pytest.raises(PipelineRunError, match="External calls are disabled"):
        run_deterministic_pipeline(
            PipelineRunConfig(
                run_id="run-1",
                domain="human geography",
                root=tmp_path,
                adapter_backend="openai",
            )
        )

    assert not (tmp_path / "runs" / "run-1").exists()


def _response() -> dict[str, object]:
    return {
        "candidates": [
            _candidate("No-data calibration map", "NoData", None),
            _candidate(
                "Synthetic calibration stress test",
                "SyntheticOnly",
                "Use a seeded shift generator and declared calibration metric.",
            ),
            _candidate("Public benchmark calibration", "PublicDownload", None),
            _candidate("Private deployment calibration", "UserProvided", None),
        ]
    }


def _candidate(
    title: str,
    data_requirement: str,
    experiment: str | None,
) -> dict[str, object]:
    return {
        "title": title,
        "domain": "machine learning",
        "method": "calibration",
        "claim_type": "methodological proposition",
        "question": f"What controlled conditions support {title.lower()}?",
        "hypothesis": "Declared assumptions identify a testable calibration boundary.",
        "assumptions": ["The calibration target and shift family are declared."],
        "primitives": ["calibration map", "shift family"],
        "data_requirement": data_requirement,
        "possible_synthetic_experiment": experiment,
        "baseline": "Compare with an uncalibrated score.",
        "risks": ["The result may be limited to controlled assumptions."],
    }
