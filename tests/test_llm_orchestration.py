from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.errors import AdapterTransportError
from factori.cli import app
from factori.evidence import is_proof_evidence, is_synthetic_experiment_evidence
from factori.full_paper_generation import FullPaperGenerationStatus
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.llm_orchestration import (
    LLMOrchestrationError,
    build_llm_orchestration_preflight_summary,
    inspect_llm_run_summary,
    run_llm_paper_orchestration,
)
from factori.manuscript_plan import planned_manuscript_section_count
from factori.output_hygiene import inspect_output_hygiene
from factori.replay import replay_verify_run
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    LLMBudgetConfig,
    LLMCallStatus,
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


def test_preflight_plans_bounded_openai_claim_adjudication_calls() -> None:
    config = LLMOrchestrationConfig(
        run_id="claim-adjudication-preflight",
        domain="human geography",
        candidate_backend="fake",
        reviewer_backend="fake",
        prose_backend="fake",
        claim_adjudicator_backend="openai",
        claim_adjudicator_model="test-model",
        allow_external_calls=True,
        budget=LLMBudgetConfig(
            max_total_calls=4,
            max_claim_adjudication_calls=4,
            max_estimated_cost_usd=1.0,
        ),
    )

    summary = build_llm_orchestration_preflight_summary(config)

    assert summary["claim_adjudicator_backend"] == "openai"
    assert summary["claim_adjudicator_model"] == "test-model"
    assert summary["claim_adjudication_calls"] == 4
    assert summary["estimated_max_calls"] == 4


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


def test_fake_orchestration_can_build_bounded_fixture_citation_registry(tmp_path) -> None:
    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="fake-retrieval-orch",
            domain="human geography",
            enable_retrieval=True,
            retrieval_backend="fake",
            max_retrieval_sources=3,
            citation_policy="registry-only",
            write_report=True,
            budget=LLMBudgetConfig(max_total_calls=0),
        ),
        root=tmp_path,
    )

    assert result.generation_result is not None
    reports = tmp_path / "runs" / "fake-retrieval-orch" / "reports"
    assert (reports / "retrieval-report.json").is_file()
    registry_payload = json.loads((reports / "citation-registry.json").read_text())
    assert registry_payload["citation_policy"] == "registry-only"
    assert registry_payload["source_count"] == 3
    assert all(
        item["source_status"] == "fixture" for item in registry_payload["citations"]
    )
    markdown = (reports / "complete-manuscript-draft.md").read_text()
    assert "[@" in markdown
    assert result.report.publication_ready is False
    assert result.report.is_verification_evidence is False


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
            "--candidate-model",
            "candidate-test-model",
            "--reviewer-model",
            "reviewer-test-model",
            "--prose-model",
            "prose-test-model",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report = payload["llm_orchestration_result"]["report"]
    assert report["publication_ready"] is False
    assert report["is_verification_evidence"] is False
    assert report["selected_backends"]["candidate_backend"] == "fake"
    assert report["selected_backends"]["candidate_model"] == "candidate-test-model"
    assert report["selected_backends"]["reviewer_backend"] == "fake"
    assert report["selected_backends"]["reviewer_model"] == "reviewer-test-model"
    assert report["selected_backends"]["prose_backend"] == "fake"
    assert report["selected_backends"]["prose_model"] == "prose-test-model"
    assert report["selected_backends"]["preflight_status"] == "Succeeded"
    assert payload["preflight_summary"]["candidate_model"] == "candidate-test-model"
    assert payload["artifacts"]["llm_orchestration_report"] is not None
    assert payload["preflight_summary"]["safe_repair_effective"] is False
    assert payload["artifacts"]["safe_repair_report"] is None


def test_run_llm_paper_cli_enable_safe_repair_writes_audit_artifact(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli-safe-repair",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--max-total-calls",
            "0",
            "--enable-safe-repair",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_summary"]["safe_repair_effective"] is True
    repair_ref = ArtifactRef.model_validate(payload["artifacts"]["safe_repair_report"])
    _assert_non_evidence_artifact(tmp_path, repair_ref)
    report = payload["llm_orchestration_result"]["report"]
    assert report["release_status"] in {
        "ReadyForHumanReview",
        "ReadyForHumanReviewWithWarnings",
    }


def test_safe_repair_filters_pre_repair_warnings_from_orchestration_report(tmp_path) -> None:
    transport = UnsafeFirstProseTransport()
    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="orchestration-safe-repair-warnings",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="openai",
            prose_model="prose-test-model",
            allow_external_calls=True,
            write_report=True,
            budget=LLMBudgetConfig(
                max_total_calls=12,
                max_prose_calls=12,
                max_estimated_cost_usd=1.50,
            ),
        ),
        root=tmp_path,
        prose_transport=transport,
        environ={"OPENAI_API_KEY": "sk-test-key"},
        enable_safe_repair=True,
    )

    assert transport.calls
    assert result.generation_result is not None
    assert result.generation_result.revision_result is not None
    repair_ref = result.generation_result.revision_result.safe_repair_report_artifact
    assert repair_ref is not None
    repair_payload = json.loads((tmp_path / repair_ref.path).read_text(encoding="utf-8"))
    assert "central message is missing or unavailable" not in repair_payload[
        "pre_repair_warnings"
    ]
    assert any(
        "LeanVerified language appears without local proof-evidence validation"
        in warning
        for warning in repair_payload["pre_repair_warnings"]
    )
    assert not any(
        warning.startswith("forbidden label appears in generated prose:")
        for warning in result.report.warnings
    )
    assert repair_payload["repaired_warnings"]
    for repaired_warning in repair_payload["repaired_warnings"]:
        assert repaired_warning in repair_payload["pre_repair_warnings"]
        assert repaired_warning not in repair_payload["post_repair_warnings"]
        assert repaired_warning not in result.report.warnings
    assert any("No citation markers were used" in warning for warning in result.report.warnings)
    assert result.report.release_status == ReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS


def test_inspect_llm_run_json_and_human_output_are_compact_and_read_only(tmp_path) -> None:
    run_id = "inspect-integrated"
    prose_calls = planned_manuscript_section_count()
    total_calls = 3 + 16 + prose_calls
    run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id=run_id,
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="openai",
            prose_backend="openai",
            allow_external_calls=True,
            write_report=True,
            budget=LLMBudgetConfig(
                max_total_calls=40,
                max_candidate_generation_calls=3,
                max_review_calls=16,
                max_prose_calls=prose_calls,
                max_estimated_cost_usd=5.0,
            ),
        ),
        root=tmp_path,
        llm_transport=CandidateTransport(),
        reviewer_transport=ReviewerTransport(),
        prose_transport=ProseTransport(),
        environ={"OPENAI_API_KEY": "sk-test-key"},
        enable_safe_repair=True,
    )
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "inspect-llm-run",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["orchestration_status"] == "LLMOrchestrationSucceededWithWarnings"
    assert payload["paper_release_status"] == "ReadyForHumanReviewWithWarnings"
    assert payload["publication_ready"] is False
    assert payload["safety_report_safe"] is True
    assert payload["candidate_generation_calls"] == 3
    assert payload["review_calls"] == 16
    assert payload["prose_calls"] == prose_calls
    assert payload["total_calls"] == total_calls
    assert payload["external_call_count"] == total_calls
    assert payload["failed_call_count"] == 0
    assert payload["blocked_call_count"] == 0
    assert payload["safe_repair_report_present"] is True
    assert payload["runtime_budget_blocked"] is False
    assert payload["artifact_paths"]["paper"].endswith("latex/paper.tex")
    assert payload["artifact_paths"]["revised_paper"].endswith("latex/revised-paper.tex")
    assert "llm_orchestration_report" in payload["artifact_paths"]

    human_result = CliRunner().invoke(
        app,
        ["inspect-llm-run", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Run: {run_id}" in human_result.output
    assert "Status: LLMOrchestrationSucceededWithWarnings" in human_result.output
    assert "Release: ReadyForHumanReviewWithWarnings" in human_result.output
    assert "Publication ready: false" in human_result.output
    assert "Safety: safe" in human_result.output
    assert (
        f"Calls: {total_calls} total = 3 candidate + 16 review + {prose_calls} prose"
        in human_result.output
    )
    assert "Runtime budget blocked: false" in human_result.output
    assert "Safe repair: present" in human_result.output
    assert "- No citation markers were used in the draft." in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_inspect_llm_run_without_safe_repair_report(tmp_path) -> None:
    run_id = "inspect-no-repair"
    run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id=run_id,
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="fake",
            write_report=True,
            budget=LLMBudgetConfig(max_total_calls=0),
        ),
        root=tmp_path,
    )

    summary = inspect_llm_run_summary(run_id=run_id, root=tmp_path)

    assert summary["safe_repair_report_present"] is False
    assert summary["total_calls"] == 0
    assert summary["external_call_count"] == 0
    assert "safe_repair_report" not in summary["artifact_paths"]


def test_inspect_llm_run_reports_budget_blocked_call(monkeypatch, tmp_path) -> None:
    import factori.llm_orchestration as module

    run_id = "inspect-budget-blocked"
    original_planned_usage = module._planned_usage

    def undercounted_usage(config, *, llm_scope="full-paper"):
        usage = original_planned_usage(config, llm_scope=llm_scope)
        return usage.model_copy(
            update={
                "total_calls": 1,
                "candidate_generation_calls": 1,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "estimated_cost_usd": 0.01,
            }
        )

    monkeypatch.setattr(module, "_planned_usage", undercounted_usage)
    module.run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id=run_id,
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            write_report=True,
            budget=LLMBudgetConfig(
                max_total_calls=1,
                max_candidate_generation_calls=1,
                max_estimated_cost_usd=1.0,
            ),
        ),
        root=tmp_path,
        llm_transport=CandidateTransport(),
        environ={"OPENAI_API_KEY": "sk-test-key"},
        llm_scope="candidate-only",
    )

    payload = json.loads(
        CliRunner()
        .invoke(
            app,
            [
                "inspect-llm-run",
                "--root",
                str(tmp_path),
                "--run-id",
                run_id,
                "--json",
            ],
        )
        .output
    )

    assert payload["orchestration_status"] == "LLMOrchestrationBlocked"
    assert payload["runtime_budget_blocked"] is True
    assert payload["external_call_count"] == 1
    assert payload["blocked_call_count"] == 1
    assert payload["failed_call_count"] == 0
    assert payload["blocking_issues"]


def test_inspect_llm_run_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "inspect-llm-run",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-run",
        ],
    )

    assert result.exit_code == 1
    assert "No LLM orchestration report found for run_id=missing-run" in result.output


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
    assert len(prose_transport.calls) == planned_manuscript_section_count()
    assert result.report.orchestration_status in {
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED,
        LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert result.report.selected_backends["candidate_backend"] == "openai"
    assert result.report.selected_backends["candidate_model"] == "gpt-5-mini"
    assert result.report.selected_backends["reviewer_backend"] == "openai"
    assert result.report.selected_backends["reviewer_model"] == "gpt-5-mini"
    assert result.report.selected_backends["prose_backend"] == "openai"
    assert result.report.selected_backends["prose_model"] == "gpt-5-mini"
    assert any(record.external_call_performed for record in result.report.call_accounting)
    assert all(record.contains_secret is False for record in result.report.call_accounting)
    assert "test-key" not in result.report.model_dump_json()


def test_run_llm_paper_preflight_only_validates_without_mutation(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "preflight",
            "--domain",
            "human geography",
            "--candidate-backend",
            "openai",
            "--candidate-model",
            "candidate-live-model",
            "--reviewer-backend",
            "openai",
            "--reviewer-model",
            "reviewer-live-model",
            "--prose-backend",
            "openai",
            "--prose-model",
            "prose-live-model",
            "--allow-external-calls",
            "--max-total-calls",
            "29",
            "--max-estimated-cost-usd",
            "1.0",
            "--preflight-only",
            "--json",
        ],
        env={"OPENAI_API_KEY": "test-key"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_summary"]["candidate_model"] == "candidate-live-model"
    assert payload["preflight_summary"]["reviewer_model"] == "reviewer-live-model"
    assert payload["preflight_summary"]["prose_model"] == "prose-live-model"
    report = payload["llm_orchestration_result"]["report"]
    assert [step["step_name"] for step in report["steps"]] == ["preflight"]
    assert report["selected_backends"]["preflight_status"] == "Succeeded"
    assert payload["artifacts"]["llm_orchestration_report"] is None
    assert not (tmp_path / "runs" / "preflight").exists()


def test_candidate_only_scope_preflight_counts_stage_a_seeded_constraints() -> None:
    config = LLMOrchestrationConfig(
        run_id="candidate-only-preflight",
        domain="human geography",
        candidate_backend="openai",
        reviewer_backend="fake",
        prose_backend="fake",
        allow_external_calls=True,
        budget=LLMBudgetConfig(max_total_calls=3, max_estimated_cost_usd=0.2),
    )

    summary = build_llm_orchestration_preflight_summary(
        config,
        llm_scope="candidate-only",
    )

    assert summary["llm_scope"] == "candidate-only"
    assert summary["estimated_max_calls"] == 3
    assert summary["candidate_generation_calls"] == 3
    assert summary["review_calls"] == 0
    assert summary["prose_calls"] == 0
    assert summary["generate_paper_effective"] is False
    assert summary["evaluate_release_effective"] is False
    assert summary["export_latex_effective"] is False

    repair_summary = build_llm_orchestration_preflight_summary(
        config,
        llm_scope="candidate-only",
        enable_safe_repair=True,
    )
    assert repair_summary["safe_repair_effective"] is False


def test_candidate_only_scope_runs_stage_a_without_paper_or_release(tmp_path) -> None:
    candidate_transport = CandidateTransport()

    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="candidate-only",
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            generate_paper=True,
            evaluate_release=True,
            write_report=True,
            budget=LLMBudgetConfig(
                max_total_calls=3,
                max_candidate_generation_calls=3,
                max_estimated_cost_usd=0.2,
            ),
        ),
        root=tmp_path,
        llm_transport=candidate_transport,
        environ={"OPENAI_API_KEY": "test-key"},
        llm_scope="candidate-only",
    )

    assert len(candidate_transport.calls) == 3
    assert result.pipeline_report is None
    assert result.generation_result is None
    assert result.release_result is None
    assert [step.step_name for step in result.report.steps] == [
        "preflight",
        "candidate-only-stage-a",
    ]
    assert "generate-paper" not in [step.step_name for step in result.report.steps]
    assert "evaluate-paper-release" not in [
        step.step_name for step in result.report.steps
    ]
    assert result.report.selected_backends["llm_scope"] == "candidate-only"
    assert result.report.selected_backends["generate_paper_effective"] == "false"
    assert result.report.selected_backends["evaluate_release_effective"] == "false"
    assert result.report.selected_backends["export_latex_effective"] == "false"
    assert result.report.generate_paper_status is None
    assert result.report.release_status is None
    assert result.report.budget_decision.planned_usage.total_calls == 3
    assert result.report.budget_usage.total_calls == 3
    succeeded = [
        record
        for record in result.report.call_accounting
        if record.status == LLMCallStatus.SUCCEEDED
    ]
    assert len(succeeded) == 3
    assert all(record.step_name == "llm-candidate-generation" for record in succeeded)
    assert all(record.external_call_performed for record in succeeded)
    assert all(record.contains_secret is False for record in result.report.call_accounting)
    assert result.report_artifact is not None
    assert "test-key" not in result.report.model_dump_json()


def test_reviewer_only_scope_plans_complete_stage_b_review_workload() -> None:
    config = LLMOrchestrationConfig(
        run_id="reviewer-only-preflight",
        domain="human geography",
        candidate_backend="openai",
        reviewer_backend="openai",
        prose_backend="openai",
        allow_external_calls=True,
        budget=LLMBudgetConfig(max_total_calls=19, max_estimated_cost_usd=1.0),
    )

    summary = build_llm_orchestration_preflight_summary(
        config,
        llm_scope="reviewer-only",
    )

    assert summary["llm_scope"] == "reviewer-only"
    assert summary["candidate_generation_calls"] == 3
    assert summary["review_calls"] == 16
    assert summary["prose_calls"] == 0
    assert summary["estimated_max_calls"] == 19
    assert summary["generate_paper_effective"] is False
    assert summary["evaluate_release_effective"] is False
    assert summary["export_latex_effective"] is False


def test_full_paper_preflight_plans_all_manuscript_prose_tasks() -> None:
    prose_calls = planned_manuscript_section_count()
    config = LLMOrchestrationConfig(
        run_id="prose-preflight",
        domain="human geography",
        candidate_backend="fake",
        reviewer_backend="fake",
        prose_backend="openai",
        allow_external_calls=True,
        budget=LLMBudgetConfig(
            max_total_calls=prose_calls,
            max_prose_calls=prose_calls,
            max_estimated_cost_usd=1.0,
        ),
    )

    summary = build_llm_orchestration_preflight_summary(config)

    assert summary["candidate_generation_calls"] == 0
    assert summary["review_calls"] == 0
    assert summary["prose_calls"] == prose_calls
    assert summary["estimated_max_calls"] == prose_calls


def test_full_paper_preflight_blocks_low_prose_budget_before_mutation(tmp_path) -> None:
    low_budget = planned_manuscript_section_count() - 1
    with pytest.raises(LLMOrchestrationError, match="max_prose_calls exceeded"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="prose-preflight-low-budget",
                domain="human geography",
                candidate_backend="fake",
                reviewer_backend="fake",
                prose_backend="openai",
                allow_external_calls=True,
                budget=LLMBudgetConfig(
                    max_total_calls=low_budget,
                    max_prose_calls=low_budget,
                    max_estimated_cost_usd=1.0,
                ),
            ),
            root=tmp_path,
            prose_transport=ProseTransport(),
            environ={"OPENAI_API_KEY": "test-key"},
        )

    assert not (tmp_path / "runs" / "prose-preflight-low-budget").exists()


def test_reviewer_only_scope_blocks_low_review_budget_before_mutation(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="max_review_calls exceeded"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="reviewer-only-low-budget",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="openai",
                prose_backend="fake",
                allow_external_calls=True,
                budget=LLMBudgetConfig(
                    max_total_calls=9,
                    max_candidate_generation_calls=3,
                    max_review_calls=6,
                    max_estimated_cost_usd=1.0,
                ),
            ),
            root=tmp_path,
            llm_transport=CandidateTransport(),
            reviewer_transport=ReviewerTransport(),
            environ={"OPENAI_API_KEY": "test-key"},
            llm_scope="reviewer-only",
        )

    assert not (tmp_path / "runs" / "reviewer-only-low-budget").exists()


def test_reviewer_only_scope_runs_stage_a_and_stage_b_only(tmp_path) -> None:
    candidate_transport = CandidateTransport()
    reviewer_transport = ReviewerTransport()

    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="reviewer-only",
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="openai",
            prose_backend="openai",
            allow_external_calls=True,
            generate_paper=True,
            evaluate_release=True,
            export_latex=True,
            critique=True,
            revise=True,
            write_report=True,
            budget=LLMBudgetConfig(
                max_total_calls=19,
                max_candidate_generation_calls=3,
                max_review_calls=16,
                max_estimated_cost_usd=1.0,
            ),
        ),
        root=tmp_path,
        llm_transport=candidate_transport,
        reviewer_transport=reviewer_transport,
        environ={"OPENAI_API_KEY": "test-key"},
        llm_scope="reviewer-only",
    )

    assert len(candidate_transport.calls) == 3
    assert len(reviewer_transport.calls) == 16
    assert result.pipeline_report is None
    assert result.generation_result is None
    assert result.release_result is None
    assert [step.step_name for step in result.report.steps] == [
        "preflight",
        "reviewer-only-stage-a",
        "reviewer-only-stage-b",
    ]
    assert result.report.selected_backends["llm_scope"] == "reviewer-only"
    assert result.report.selected_backends["generate_paper_effective"] == "false"
    assert result.report.selected_backends["evaluate_release_effective"] == "false"
    assert result.report.selected_backends["export_latex_effective"] == "false"
    assert result.report.budget_decision.planned_usage.review_calls == 16
    assert result.report.budget_usage.review_calls == 16
    assert result.report.generate_paper_status is None
    assert result.report.release_status is None


def test_run_llm_paper_cli_accepts_reviewer_only_scope(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli-reviewer-only",
            "--domain",
            "human geography",
            "--llm-scope",
            "reviewer-only",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--max-total-calls",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_summary"]["llm_scope"] == "reviewer-only"
    assert payload["preflight_summary"]["generate_paper_effective"] is False
    assert payload["preflight_summary"]["evaluate_release_effective"] is False
    assert payload["preflight_summary"]["export_latex_effective"] is False
    assert [
        step["step_name"]
        for step in payload["llm_orchestration_result"]["report"]["steps"]
    ] == ["preflight", "reviewer-only-stage-a", "reviewer-only-stage-b"]


def test_full_paper_stops_after_stage_b_runtime_budget_failure(
    monkeypatch,
    tmp_path,
) -> None:
    import factori.llm_orchestration as module

    original_planned_usage = module._planned_usage
    generated = []

    def undercounted_usage(config, *, llm_scope="full-paper"):
        usage = original_planned_usage(config, llm_scope=llm_scope)
        return usage.model_copy(
            update={
                "total_calls": 4,
                "review_calls": 1,
                "total_input_tokens": 4000,
                "total_output_tokens": 2000,
                "estimated_cost_usd": 0.04,
            }
        )

    monkeypatch.setattr(module, "_planned_usage", undercounted_usage)
    monkeypatch.setattr(
        module,
        "generate_full_paper",
        lambda **kwargs: generated.append(kwargs),
    )

    result = module.run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="full-paper-stage-b-budget-block",
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="openai",
            prose_backend="fake",
            allow_external_calls=True,
            budget=LLMBudgetConfig(
                max_total_calls=8,
                max_candidate_generation_calls=3,
                max_review_calls=5,
                max_estimated_cost_usd=1.0,
            ),
        ),
        root=tmp_path,
        llm_transport=CandidateTransport(),
        reviewer_transport=ReviewerTransport(),
        environ={"OPENAI_API_KEY": "test-key"},
        llm_scope="full-paper",
    )

    assert generated == []
    assert result.report.orchestration_status == LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
    assert result.report.selected_backends["runtime_budget_blocked"] == "true"
    steps = {step.step_name: step for step in result.report.steps}
    assert steps["run-all"].status.value == "Blocked"
    assert steps["generate-paper"].status.value == "Skipped"
    assert "upstream LLM budget failed" in steps["generate-paper"].summary
    blocked = [
        record
        for record in result.report.call_accounting
        if record.status == LLMCallStatus.BLOCKED
    ]
    assert len(blocked) == 1
    assert blocked[0].error_type == "BudgetExceeded"
    assert blocked[0].external_call_performed is False


def test_cli_json_catches_runtime_prose_budget_failure(monkeypatch, tmp_path) -> None:
    import factori.cli as cli_module
    import factori.llm_orchestration as module

    original_planned_usage = module._planned_usage
    real_orchestration = module.run_llm_paper_orchestration
    prose_transport = ProseTransport()
    low_budget = planned_manuscript_section_count() - 1

    def undercounted_usage(config, *, llm_scope="full-paper"):
        usage = original_planned_usage(config, llm_scope=llm_scope)
        return usage.model_copy(
            update={
                "total_calls": 1,
                "prose_calls": 1,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "estimated_cost_usd": 0.01,
            }
        )

    def run_with_injected_transport(**kwargs):
        return real_orchestration(
            **kwargs,
            prose_transport=prose_transport,
            environ={"OPENAI_API_KEY": "test-key"},
        )

    monkeypatch.setattr(module, "_planned_usage", undercounted_usage)
    monkeypatch.setattr(
        cli_module,
        "run_llm_paper_orchestration",
        run_with_injected_transport,
    )

    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli-prose-runtime-budget-block",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "openai",
            "--allow-external-calls",
            "--max-total-calls",
            str(low_budget),
            "--max-prose-calls",
            str(low_budget),
            "--max-estimated-cost-usd",
            "1.0",
            "--skip-evaluate-release",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    report = payload["llm_orchestration_result"]["report"]
    assert report["orchestration_status"] == "LLMOrchestrationBlocked"
    assert report["selected_backends"]["runtime_budget_blocked"] == "true"
    generate_step = next(
        step for step in report["steps"] if step["step_name"] == "generate-paper"
    )
    assert generate_step["status"] == "Blocked"
    assert "max_prose_calls exceeded" in generate_step["error_message"]
    blocked = [record for record in report["call_accounting"] if record["status"] == "Blocked"]
    assert len(blocked) == 1
    assert blocked[0]["step_name"] == "llm-prose-generation"
    assert blocked[0]["error_type"] == "BudgetExceeded"
    assert blocked[0]["external_call_performed"] is False
    assert len(prose_transport.calls) == low_budget
    assert payload["artifacts"]["llm_orchestration_report"] is not None


def test_candidate_only_scope_budget_blocks_before_any_transport_call(tmp_path) -> None:
    candidate_transport = CandidateTransport()

    with pytest.raises(LLMOrchestrationError, match="max_total_calls exceeded"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="candidate-only-over-budget",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="fake",
                prose_backend="fake",
                allow_external_calls=True,
                budget=LLMBudgetConfig(
                    max_total_calls=1,
                    max_candidate_generation_calls=1,
                    max_estimated_cost_usd=0.2,
                ),
            ),
            root=tmp_path,
            llm_transport=candidate_transport,
            environ={"OPENAI_API_KEY": "test-key"},
            llm_scope="candidate-only",
        )

    assert candidate_transport.calls == []
    assert not (tmp_path / "runs" / "candidate-only-over-budget").exists()


def test_run_llm_paper_cli_candidate_only_scope_is_json_visible(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli-candidate-only",
            "--domain",
            "human geography",
            "--llm-scope",
            "candidate-only",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--max-total-calls",
            "0",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report = payload["llm_orchestration_result"]["report"]
    assert payload["preflight_summary"]["llm_scope"] == "candidate-only"
    assert payload["preflight_summary"]["generate_paper_effective"] is False
    assert report["selected_backends"]["llm_scope"] == "candidate-only"
    assert report["generate_paper_status"] is None
    assert report["release_status"] is None
    assert [step["step_name"] for step in report["steps"]] == [
        "preflight",
        "candidate-only-stage-a",
    ]


def test_pipeline_transport_error_reports_sanitized_openai_body(tmp_path) -> None:
    result = run_llm_paper_orchestration(
        config=LLMOrchestrationConfig(
            run_id="transport-error",
            domain="human geography",
            candidate_backend="openai",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            generate_paper=False,
            evaluate_release=False,
            budget=LLMBudgetConfig(max_total_calls=3, max_estimated_cost_usd=0.2),
        ),
        root=tmp_path,
        llm_transport=FailingTransport(),
        environ={"OPENAI_API_KEY": "test-key"},
    )

    assert result.report.orchestration_status == LLMOrchestrationStatus.ORCHESTRATION_FAILED
    assert "Unsupported parameter" in result.report.model_dump_json()
    assert "sk-live-secret" not in result.report.model_dump_json()
    failed_records = [
        record
        for record in result.report.call_accounting
        if record.status == LLMCallStatus.FAILED
    ]
    assert failed_records
    assert failed_records[0].error_type == "AdapterTransportError"


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


def _run_file_snapshot(tmp_path, run_id: str) -> dict[str, str]:
    run_path = tmp_path / "runs" / run_id
    return {
        path.relative_to(run_path).as_posix(): sha256_file(path)
        for path in sorted(run_path.rglob("*"))
        if path.is_file()
    }


class FailingTransport:
    def create_response(self, **kwargs: Any) -> dict[str, object]:
        del kwargs
        raise AdapterTransportError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            status_code=400,
            url="https://api.openai.com/v1/responses?api_key=sk-live-secret",
            message='HTTP 400; body={"error":{"message":"Unsupported parameter"}}',
            response_body_excerpt='{"error":{"message":"Unsupported parameter"}}',
        )


@dataclass
class CandidateTransport:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_response(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)
        prompt_payload = json.loads(str(kwargs["prompt"]).split("\n", 1)[1])
        domain = prompt_payload.get("domain") or "machine learning"
        method = prompt_payload.get("method") or "calibration"
        title_prefix = str(method).title()
        return {
            "candidates": [
                _candidate(f"No-data {title_prefix} map", "NoData", None, domain, method),
                _candidate(
                    f"Synthetic {title_prefix} stress test",
                    "SyntheticOnly",
                    "Use a seeded shift generator and declared calibration metric.",
                    domain,
                    method,
                ),
                _candidate(
                    f"Public benchmark {title_prefix}",
                    "PublicDownload",
                    None,
                    domain,
                    method,
                ),
                _candidate(
                    f"Private deployment {title_prefix}",
                    "UserProvided",
                    None,
                    domain,
                    method,
                ),
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


@dataclass
class UnsafeFirstProseTransport(ProseTransport):
    unsafe_emitted: bool = False

    def create_response(self, **kwargs: Any) -> dict[str, object]:
        payload = super().create_response(**kwargs)
        if self.unsafe_emitted:
            return payload
        self.unsafe_emitted = True
        payload["draft_markdown"] = (
            "Conjecture. The synthetic result is empirically validated. "
            "This unsupported sentence is intentionally unsafe."
        )
        payload["unsupported_sentences"] = [
            "This unsupported sentence is intentionally unsafe."
        ]
        return payload


def _candidate(
    title: str,
    data_requirement: str,
    experiment: str | None,
    domain: str = "machine learning",
    method: str = "calibration",
) -> dict[str, object]:
    return {
        "title": title,
        "domain": domain,
        "method": method,
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
