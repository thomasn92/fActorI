from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.llm_review import OpenAIReviewerClient
from factori.adapters.registry import AdapterConfigurationError, get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schemas import ConstraintSet, ControllerActionType, PipelineRunConfig, PipelineStage
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b


@dataclass
class ReviewerTransport:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        candidate_id = json.loads(kwargs["prompt"])["candidate"]["candidate_id"]
        return {"reviews": [_review(candidate_id, index) for index in range(3)]}


def test_llm_reviewer_backend_is_gated_before_transport() -> None:
    transport = ReviewerTransport()

    with pytest.raises(AdapterConfigurationError, match="External calls are disabled"):
        get_adapter_registry(
            AdapterConfig(
                reviewer_backend="openai",
                use_llm_reviewers=True,
                reviewer_api_key="test-key",
            ),
            reviewer_transport=transport,
        )

    assert transport.calls == []


def test_llm_reviewer_backend_requires_api_key() -> None:
    with pytest.raises(AdapterConfigurationError, match="no API key is configured"):
        get_adapter_registry(
            AdapterConfig(
                reviewer_backend="openai",
                use_llm_reviewers=True,
                allow_external_calls=True,
            ),
            environ={},
        )


def test_invalid_reviewer_backend_fails_clearly() -> None:
    with pytest.raises(AdapterConfigurationError, match="Reviewer backend 'unknown'"):
        get_adapter_registry(AdapterConfig(reviewer_backend="unknown"))


def test_openai_reviewer_uses_injected_transport_deterministically() -> None:
    transport = ReviewerTransport()
    client = OpenAIReviewerClient(
        api_key="test-key",
        model="test-model",
        transport=transport,
        allow_external_calls=True,
    )
    candidate = _candidate()

    first = client.review_candidate(candidate, {"clarity": "Assess precision"})
    second = client.review_candidate(candidate, {"clarity": "Assess precision"})

    assert first == second
    assert len(first.reports) == 3
    assert all(not report.fake for report in first.reports)
    assert all(not report.is_verification_evidence for report in first.reports)
    assert len(transport.calls) == 2
    assert client.review_traces[0].request["api_key_recorded"] is False
    assert client.review_traces[0].request["reviewer_has_verification_authority"] is False


def test_stage_b_llm_review_writes_ledgered_non_evidence_artifacts(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    stage_a = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        store=store,
        ledger=ledger,
    )
    assert stage_a.survivors
    transport = ReviewerTransport()
    registry = get_adapter_registry(
        AdapterConfig(
            reviewer_backend="openai",
            use_llm_reviewers=True,
            allow_external_calls=True,
            reviewer_api_key="test-key",
            reviewer_model="test-model",
        ),
        reviewer_transport=transport,
    )

    result = run_stage_b(
        run_id="run-1",
        store=store,
        ledger=ledger,
        reviewer_client=registry.reviewer,
    )

    assert len(transport.calls) == len(result.children)
    assert len(result.llm_reviewer_artifacts) == len(result.children) * 3
    assert all(ref.content_hash for ref in result.llm_reviewer_artifacts)
    assert all(ref.producing_commit_hash for ref in result.llm_reviewer_artifacts)
    assert all(
        ref.metadata["is_verification_evidence"] is False
        for ref in result.llm_reviewer_artifacts
    )
    assert all(not ref.is_mvp_verification_evidence() for ref in result.llm_reviewer_artifacts)
    assert result.reviewer_adapter_metadata["backend"] == "openai"
    assert result.reviewer_adapter_metadata["model"] == "test-model"
    assert any(
        commit.action_type == ControllerActionType.STAGE_B_LLM_REVIEW_RECORDED
        for commit in ledger.list_commits("run-1")
    )
    report = (tmp_path / result.report_artifact.path).read_text(encoding="utf-8")
    assert "Reviewer adapter: openai" in report
    assert "Reviewer model: test-model" in report
    assert "no verification authority" in report

    manifest = build_artifact_manifest("run-1", store)
    entries = [
        entry
        for entry in manifest.artifacts
        if entry.artifact_id.startswith("llm-stage-b-reviewer-")
    ]
    assert len(entries) == len(result.children) * 3
    assert all(not entry.is_evidence for entry in entries)


def test_default_stage_b_remains_fake(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        store=store,
        ledger=ledger,
    )

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)

    assert result.reviewer_adapter_metadata["backend"] == "fake"
    assert result.llm_reviewer_artifacts == []
    assert all(report.fake for panel in result.reviewer_panels.values() for report in panel.reports)


def test_stage_b_cli_requires_explicit_external_opt_in_without_mutation(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-stage-b",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--reviewer-backend",
            "openai",
            "--use-llm-reviewers",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr
    assert not (tmp_path / "runs" / "run-1").exists()


def test_run_all_llm_reviewers_require_explicit_external_opt_in(tmp_path) -> None:
    with pytest.raises(PipelineRunError, match="External calls are disabled"):
        run_deterministic_pipeline(
            PipelineRunConfig(
                run_id="run-1",
                domain="machine learning",
                method="calibration",
                root=tmp_path,
                reviewer_backend="openai",
                use_llm_reviewers=True,
                stop_after=PipelineStage.RUN_STAGE_B,
            )
        )

    assert not (tmp_path / "runs" / "run-1").exists()


def _candidate():
    from factori.schemas import Candidate, DataRequirement

    constraints = ConstraintSet(
        domain="machine learning",
        method="calibration",
        question="Can a seeded calibration contract survive distribution shift?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    return Candidate(
        id="candidate-1",
        constraints=constraints,
        domain=constraints.domain,
        method=constraints.method,
        question=constraints.question or "Calibration question",
        data_requirement=constraints.data_requirement,
    )


def _review(candidate_id: str, index: int) -> dict[str, object]:
    score = 0.72 + index * 0.01
    return {
        "reviewer_id": f"llm-reviewer-{index + 1}",
        "candidate_id": candidate_id,
        "novelty_score": score,
        "feasibility_score": score,
        "verifiability_score": score,
        "clarity_score": score,
        "significance_score": score,
        "objections": ["Clarify the declared structural assumptions."],
        "recommendation": "WeakAccept",
    }
