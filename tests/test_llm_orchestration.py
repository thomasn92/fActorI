from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.cli import app
from factori.evidence import is_proof_evidence, is_synthetic_experiment_evidence
from factori.full_paper_generation import FullPaperGenerationStatus
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.llm_orchestration import (
    LLMOrchestrationError,
    run_llm_paper_orchestration,
)
from factori.output_hygiene import inspect_output_hygiene
from factori.replay import replay_verify_run
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    LLMBudgetConfig,
    LLMOrchestrationConfig,
    LLMOrchestrationReport,
    LLMOrchestrationStatus,
    OutputHygieneStatus,
    ReplayStatus,
)
from factori.schemas import (
    FullPaperReleaseStatus as ReleaseStatus,
)


def test_llm_orchestration_models_are_importable() -> None:
    assert LLMOrchestrationConfig
    assert LLMOrchestrationReport
    assert LLMBudgetConfig


def test_fake_llm_orchestration_runs_without_network_and_writes_reports(tmp_path) -> None:
    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="fake-orch",
            domain="human geography",
            apply_safe_fake_revision=True,
            reexport_latex_after_revision=True,
            write_report=True,
            budget=LLMBudgetConfig(max_total_calls=0),
        ),
        root=tmp_path,
    )

    assert result.report.orchestration_status in {
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED,
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert result.generation_result is not None
    assert result.generation_result.report.generation_status in {
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED,
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert result.release_result is not None
    assert result.release_result.report.decision.status in {
        ReleaseStatus.READY_FOR_HUMAN_REVIEW,
        ReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
    }
    assert result.report.publication_ready is False
    assert result.report.is_verification_evidence is False
    assert result.report.safety_report.llm_outputs_are_verification_evidence is False
    assert result.report.budget_usage.total_calls == 0
    assert {record.status.value for record in result.report.call_accounting} == {"Skipped"}
    assert all(record.contains_secret is False for record in result.report.call_accounting)

    for ref in (
        result.config_artifact,
        result.budget_artifact,
        result.accounting_artifact,
        result.report_artifact,
        result.safety_artifact,
    ):
        assert ref is not None
        _assert_non_evidence_artifact(tmp_path, ref)

    ledger = ResearchLedger(tmp_path / "runs" / "fake-orch" / "ledger.sqlite")
    actions = [commit.action_type for commit in ledger.list_commits("fake-orch")]
    assert actions[-1] == ControllerActionType.LLM_ORCHESTRATION_WRITTEN

    replay = replay_verify_run("fake-orch", tmp_path)
    assert replay.replay_status == ReplayStatus.REPLAY_VERIFIED
    hygiene = inspect_output_hygiene("fake-orch", tmp_path)
    assert hygiene.hygiene_status in {
        OutputHygieneStatus.CLEAN,
        OutputHygieneStatus.CLEAN_WITH_WARNINGS,
    }


def test_run_llm_paper_cli_works_in_fake_mode_and_json_is_valid(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli-fake",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report = payload["llm_orchestration_result"]["report"]
    assert report["publication_ready"] is False
    assert report["is_verification_evidence"] is False
    assert report["selected_backends"] == {
        "candidate_backend": "fake",
        "prose_backend": "fake",
        "reviewer_backend": "fake",
    }
    assert payload["artifacts"]["llm_orchestration_report"] is not None


def test_real_orchestration_fails_when_external_calls_disabled(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="allow_external_calls=false"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="blocked",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="openai",
                prose_backend="openai",
                budget=LLMBudgetConfig(max_total_calls=50, max_estimated_cost_usd=5.0),
            ),
            root=tmp_path,
        )

    assert not (tmp_path / "runs" / "blocked").exists()


def test_real_orchestration_fails_without_explicit_budget(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="Explicit LLM budget"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="no-budget",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="openai",
                prose_backend="openai",
                allow_external_calls=True,
            ),
            root=tmp_path,
            environ={"OPENAI_API_KEY": "test-key"},
        )

    assert not (tmp_path / "runs" / "no-budget").exists()


def test_real_orchestration_fails_without_api_key_after_budget(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="no API key is configured"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="no-key",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="openai",
                prose_backend="openai",
                allow_external_calls=True,
                budget=LLMBudgetConfig(max_total_calls=50, max_estimated_cost_usd=5.0),
            ),
            root=tmp_path,
            environ={},
        )

    assert not (tmp_path / "runs" / "no-key").exists()


def test_real_orchestration_budget_blocks_over_limit_before_mutation(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="max_total_calls exceeded"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="over-budget",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="openai",
                prose_backend="openai",
                allow_external_calls=True,
                budget=LLMBudgetConfig(max_total_calls=2, max_estimated_cost_usd=5.0),
            ),
            root=tmp_path,
            environ={"OPENAI_API_KEY": "test-key"},
        )

    assert not (tmp_path / "runs" / "over-budget").exists()


def test_run_llm_paper_real_mode_cli_fails_closed_without_permission(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "real-cli",
            "--domain",
            "human geography",
            "--candidate-backend",
            "openai",
            "--reviewer-backend",
            "openai",
            "--prose-backend",
            "openai",
            "--max-total-calls",
            "50",
            "--max-estimated-cost-usd",
            "5.0",
        ],
    )

    assert result.exit_code == 1
    assert "allow_external_calls=false" in result.stderr
    assert not (tmp_path / "runs" / "real-cli").exists()


def test_real_orchestration_uses_injected_transports_without_network(tmp_path) -> None:
    candidate_transport = CandidateTransport()
    reviewer_transport = ReviewerTransport()
    prose_transport = ProseTransport()

    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="real-injected",
            domain="machine learning",
            method="calibration",
            candidate_backend="openai",
            reviewer_backend="openai",
            prose_backend="openai",
            allow_external_calls=True,
            apply_safe_fake_revision=True,
            reexport_latex_after_revision=True,
            write_report=True,
            budget=LLMBudgetConfig(max_total_calls=50, max_estimated_cost_usd=5.0),
        ),
        root=tmp_path,
        llm_transport=candidate_transport,
        reviewer_transport=reviewer_transport,
        prose_transport=prose_transport,
        environ={"OPENAI_API_KEY": "test-key"},
    )

    assert candidate_transport.calls
    assert reviewer_transport.calls
    assert prose_transport.calls
    assert result.report.orchestration_status in {
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED,
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert result.report.selected_backends == {
        "candidate_backend": "openai",
        "reviewer_backend": "openai",
        "prose_backend": "openai",
    }
    assert any(record.external_call_performed for record in result.report.call_accounting)
    assert all(record.contains_secret is False for record in result.report.call_accounting)
    assert "test-key" not in result.report.model_dump_json()


def test_release_blocking_status_blocks_orchestration_success(monkeypatch, tmp_path) -> None:
    class BlockedRelease:
        report = type(
            "Report",
            (),
            {
                "decision": type(
                    "Decision",
                    (),
                    {
                        "status": ReleaseStatus.BLOCKED_CRITIC_FINDINGS,
                        "blocking_reasons": ["critic blocked"],
                        "warnings": [],
                    },
                )()
            },
        )()

    import factori.llm_orchestration as module

    monkeypatch.setattr(module, "run_full_paper_release_gate", lambda **kwargs: BlockedRelease())
    result = module.run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="blocked-release",
            domain="human geography",
            budget=LLMBudgetConfig(max_total_calls=0),
        ),
        root=tmp_path,
    )

    assert result.report.orchestration_status == LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
    assert "critic blocked" in result.report.blocking_issues


def _assert_non_evidence_artifact(tmp_path, ref: ArtifactRef) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.type == ArtifactType.REPORT
    assert ref.content_hash == sha256_file(path)
    assert is_proof_evidence(ref) is False
    assert is_synthetic_experiment_evidence(ref) is False
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


@dataclass
class CandidateTransport:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)
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


@dataclass
class ReviewerTransport:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)
        candidate_id = json.loads(kwargs["prompt"])["candidate"]["candidate_id"]
        return {"reviews": [_review(candidate_id, index) for index in range(3)]}


@dataclass
class ProseTransport:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)
        prompt = json.loads(kwargs["prompt"])
        section = prompt["section_contract"]
        return {
            "section_id": section["section_id"],
            "title": section["section_title"],
            "draft_markdown": (
                f"[FAKE TRANSPORT PROSE] {section['section_title']} is drafted from "
                "allowed contract inputs only. Generated prose is not evidence."
            ),
            "used_claim_ids": section.get("allowed_claim_ids", [])[:1],
            "used_evidence_artifact_ids": (
                section.get("allowed_evidence_artifact_ids", [])[:1]
            ),
            "used_citation_ids": section.get("allowed_citation_ids", [])[:1],
            "used_citation_keys": section.get("allowed_citation_keys", [])[:1],
            "unsupported_sentences": [],
            "warnings": ["injected transport; no network call"],
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
