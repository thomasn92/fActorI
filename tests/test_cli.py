from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_paper_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.llm_orchestration import build_llm_orchestration_preflight_summary
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    FullPaperGenerationConfig,
    FullPaperReleaseGateConfig,
    LLMBudgetConfig,
    LLMOrchestrationConfig,
    PipelineRunConfig,
    PipelineStage,
    RetrievalQualityReport,
)


def test_inspect_llm_run_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-llm-run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_inspect_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_run_llm_paper_accepts_fake_claim_adjudicator_preflight_without_mutation(
    tmp_path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "adjudicator-preflight",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--claim-adjudicator-backend",
            "fake",
            "--claim-adjudicator-model",
            "test-model",
            "--source-relevance-adjudicator-backend",
            "fake",
            "--source-relevance-adjudicator-model",
            "test-source-model",
            "--max-claim-adjudication-calls",
            "2",
            "--max-source-relevance-adjudication-calls",
            "3",
            "--preflight-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    preflight = payload["preflight_summary"]
    assert preflight["claim_adjudicator_backend"] == "fake"
    assert preflight["claim_adjudicator_model"] == "test-model"
    assert preflight["source_relevance_adjudicator_backend"] == "fake"
    assert preflight["source_relevance_adjudicator_model"] == "test-source-model"
    assert preflight["source_relevance_adjudication_calls"] == 0
    assert not (tmp_path / "runs" / "adjudicator-preflight").exists()


def test_preflight_budget_plans_openai_source_relevance_calls() -> None:
    summary = build_llm_orchestration_preflight_summary(
        LLMOrchestrationConfig(
            run_id="source-relevance-preflight",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            enable_retrieval=True,
            retrieval_backend="local",
            retrieval_local_path="tests/fixtures/retrieval/openalex_style_human_geography_sources.json",
            source_relevance_adjudicator_backend="openai",
            source_relevance_adjudicator_model="test-source-model",
            budget=LLMBudgetConfig(
                max_total_calls=4,
                max_source_relevance_adjudication_calls=4,
                max_total_input_tokens=4000,
                max_total_output_tokens=2000,
                max_estimated_cost_usd=1.0,
            ),
        )
    )

    assert summary["source_relevance_adjudicator_backend"] == "openai"
    assert summary["source_relevance_adjudication_calls"] == 4
    assert summary["estimated_max_calls"] == 4


def test_lint_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["lint-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output
    assert "--min-words" in result.output
    assert "--min-avg-words-per-section" in result.output
    assert "--min-citation-markers" in result.output


def test_inspect_paper_bundle_with_revised_artifacts_is_read_only(tmp_path) -> None:
    run_id = "inspect-paper-revised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
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
    assert payload["paper_exists"] is True
    assert payload["revised_paper_exists"] is True
    assert payload["complete_manuscript_draft_exists"] is True
    assert payload["revised_manuscript_draft_exists"] is True
    assert payload["latex_exists"] is True
    assert payload["revised_latex_exists"] is True
    assert payload["safe_repair_report_exists"] is True
    assert payload["release_report_exists"] is True
    assert payload["generation_report_exists"] is True
    assert payload["primary_artifact_to_read"].endswith(
        "reports/revised-manuscript-draft.md"
    )
    assert payload["primary_latex_to_read"].endswith("latex/revised-paper.tex")
    assert payload["line_count"] > 0
    assert payload["word_count"] > 0
    assert payload["section_count"] > 0
    assert payload["main_body_section_count"] > 0
    assert payload["appendix_section_count"] >= 2
    assert payload["total_heading_count"] == payload["section_count"]
    assert payload["section_headings_detected"]
    assert payload["title_detected"]
    assert payload["safe_repair_applied_count"] >= 0
    assert payload["citations_present"] is False
    assert payload["artifacts"]["revised_paper"].endswith("latex/revised-paper.tex")

    human_result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper bundle: {run_id}" in human_result.output
    assert "Primary draft: revised-manuscript-draft.md" in human_result.output
    assert "Release: ReadyForHumanReviewWithWarnings" in human_result.output
    assert "Safe repair: present" in human_result.output
    assert "Main-body sections:" in human_result.output
    assert "Appendix sections:" in human_result.output
    assert "Total headings:" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Artifacts:" in human_result.output
    assert "- revised manuscript:" in human_result.output
    assert "- revised latex:" in human_result.output
    assert "- release report:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_inspect_paper_bundle_without_revised_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "inspect-paper-unrevised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=False, release=False)

    summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert summary["paper_exists"] is True
    assert summary["revised_paper_exists"] is False
    assert summary["complete_manuscript_draft_exists"] is True
    assert summary["revised_manuscript_draft_exists"] is False
    assert summary["safe_repair_report_exists"] is False
    assert summary["release_report_exists"] is False
    assert summary["generation_report_exists"] is True
    assert summary["primary_artifact_to_read"].endswith(
        "reports/complete-manuscript-draft.md"
    )
    assert summary["primary_latex_to_read"].endswith("latex/paper.tex")
    assert "safe_repair_report" not in summary["artifacts"]
    assert summary["release_status"] is None


def test_inspect_paper_bundle_reports_registry_backed_citations(tmp_path) -> None:
    run_id = "inspect-paper-citations"
    markdown = _acceptable_markdown(include_citation=True) + (
        "\n## Bibliography\n\n- [@Smith2024] Fixture metadata only.\n"
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert summary["citation_registry_present"] is True
    assert summary["citation_registry_source_count"] == 1
    assert summary["registry_backed_citation_count"] >= 1
    assert summary["unregistered_citation_keys"] == []
    assert summary["bibliography_status"] == "registry-backed"
    assert summary["citation_policy"] == "registry-only"
    assert summary["claim_support_audit_present"] is True
    assert summary["claim_support_registry_supported_count"] == 1
    assert summary["claim_support_missing_required_citation_count"] == 0

    result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert result.exit_code == 0, result.output
    assert "Citation registry: present" in result.output
    assert "Registry sources: 1" in result.output
    assert "Bibliography: registry-backed" in result.output
    assert "Claim support: present" in result.output
    assert "Registry-supported claims: 1" in result.output


def test_inspect_and_lint_paper_bundle_report_retrieval_quality(tmp_path) -> None:
    run_id = "inspect-paper-retrieval-quality"
    markdown = _acceptable_markdown(include_citation=True) + (
        "\n## Bibliography\n\n- [@Smith2024] Fixture metadata only.\n"
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)
    _write_fixture_retrieval_quality_report(tmp_path, run_id)

    inspect_summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint_summary = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert inspect_summary["retrieval_quality_report_present"] is True
    assert inspect_summary["retrieved_source_count"] == 3
    assert inspect_summary["accepted_source_count"] == 1
    assert inspect_summary["rejected_source_count"] == 2
    assert inspect_summary["retrieval_adequacy_status"] == "bounded_context_only"
    assert inspect_summary["source_relevance_adjudication_enabled"] is True
    assert inspect_summary["source_relevance_adjudicator_backend"] == "fake"
    assert inspect_summary["source_relevance_adjudicated_count"] == 2
    assert inspect_summary["source_relevance_llm_accepted_count"] == 1
    assert inspect_summary["source_relevance_llm_rejected_count"] == 1
    assert inspect_summary["source_relevance_hard_reject_count"] == 1
    assert lint_summary["retrieval_quality_report_present"] is True
    assert lint_summary["source_relevance_adjudication_enabled"] is True
    assert lint_summary["source_relevance_adjudicator_backend"] == "fake"
    assert lint_summary["source_relevance_adjudicated_count"] == 2
    assert lint_summary["source_relevance_llm_accepted_count"] == 1
    assert lint_summary["source_relevance_llm_rejected_count"] == 1
    assert lint_summary["source_relevance_hard_reject_count"] == 1
    assert lint_summary["citation_registry_sources_all_accepted"] is True
    assert lint_summary["accepted_source_count"] == 1
    assert any(
        "retrieved sources were rejected" in warning
        for warning in lint_summary["development_warnings"]
    )

    result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert result.exit_code == 0, result.output
    assert "Retrieval quality: present" in result.output
    assert "Accepted sources: 1" in result.output
    assert "Rejected sources: 2" in result.output
    assert "Source relevance adjudication: fake" in result.output
    assert "Adjudicated sources: 2" in result.output
    assert "LLM accepted sources: 1" in result.output
    assert "LLM rejected sources: 1" in result.output
    assert "Hard rejected sources: 1" in result.output


def test_inspect_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_fails_placeholder_short_draft_read_only(tmp_path) -> None:
    run_id = "lint-paper-placeholder"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_placeholder_markdown(),
    )
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
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
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["word_count"] < 1500
    assert payload["section_count"] > 10
    assert payload["title_is_placeholder"] is True
    assert payload["citations_present"] is False
    assert payload["too_many_sections_for_length"] is True
    assert payload["publication_ready"] is False
    assert payload["is_verification_evidence"] is False
    assert "Draft may be skeletal: below proxy word-count target." in payload["warnings"]
    assert "Title appears to be a placeholder." in payload["issues"]
    assert "Severe section fragmentation is present." in payload["issues"]
    assert "Too many headings for the amount of content." in payload["warnings"]
    assert "No citation markers found." in payload["issues"]
    assert "No citation markers found." in payload["warnings"]
    assert payload["semantic_checks"]["central_contribution_present"] is False

    human_result = CliRunner().invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper quality: {run_id}" in human_result.output
    assert "Status: DraftQualityFailed" in human_result.output
    assert "Title: placeholder" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Semantic essentials:" in human_result.output
    assert "Quality failures:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_lint_paper_bundle_warns_on_missing_citations(tmp_path) -> None:
    run_id = "lint-paper-no-citations"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=False),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["citation_marker_count"] == 0
    assert payload["blocking_quality_issues"] == []
    assert payload["warnings"] == ["No citation markers found."]
    assert "No citation markers found." in payload["issues"]
    assert payload["semantic_checks"]["central_contribution_present"] is True


def test_lint_paper_bundle_is_not_length_only(tmp_path) -> None:
    run_id = "lint-paper-short-semantic"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_semantically_complete_markdown(include_citation=False),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["word_count"] < payload["thresholds"]["min_words"]
    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["quality_failure_reasons"] == []
    assert "Draft may be skeletal: below proxy word-count target." in payload[
        "development_warnings"
    ]
    assert payload["semantic_checks"]["problem_statement_present"] is True
    assert payload["semantic_checks"]["central_contribution_present"] is True
    assert payload["semantic_checks"]["evidence_boundary_statement_present"] is True
    assert payload["main_body_section_count"] == 5
    assert payload["appendix_section_count"] == 2
    assert payload["main_body_heading_fragmentation_detected"] is False
    assert payload["appendix_headings_present"] is True
    assert payload["semantic_section_audit"]
    assert all(
        item["is_verification_evidence"] is False
        for item in payload["semantic_section_audit"]
    )


def test_lint_paper_bundle_appendices_do_not_trigger_fragmentation_failure(
    tmp_path,
) -> None:
    run_id = "lint-paper-appendices-not-fragmented"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_seven_main_sections_with_appendices_markdown(),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["main_body_section_count"] == 7
    assert payload["appendix_section_count"] == 3
    assert payload["total_heading_count"] == 11
    assert payload["main_body_heading_fragmentation_detected"] is False
    assert payload["heading_fragmentation_detected"] is False
    assert payload["too_many_sections_for_length"] is False
    assert payload["unplanned_main_body_headings"] == []
    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["quality_failure_reasons"] == []
    assert "Severe section fragmentation is present." not in payload["issues"]
    assert (
        "Appendices increase the total heading count but do not fragment the main body."
        in payload["development_warnings"]
    )


def test_lint_paper_bundle_detects_standalone_central_message_as_metadata(
    tmp_path,
) -> None:
    run_id = "lint-paper-central-message"
    markdown = _short_semantically_complete_markdown(
        include_citation=False
    ).replace(
        "## Claim and Evidence Boundaries",
        "## Central Message\n\n"
        "The central contribution of this draft remains bounded and non-evidential.\n\n"
        "## Claim and Evidence Boundaries",
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["standalone_central_message_detected"] is True
    assert payload["metadata_section_count"] == 1
    assert payload["heading_fragmentation_detected"] is True
    assert payload["main_body_section_count"] == 5
    assert "Severe section fragmentation is present." not in payload[
        "quality_failure_reasons"
    ]


def test_lint_paper_bundle_fails_missing_central_contribution(tmp_path) -> None:
    run_id = "lint-paper-missing-contribution"
    markdown = _short_semantically_complete_markdown(include_citation=False).replace(
        "The central contribution of this draft",
        "The internal package",
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "Central contribution is missing or not explicit." in payload[
        "quality_failure_reasons"
    ]


def test_lint_paper_bundle_fails_missing_problem_statement(tmp_path) -> None:
    run_id = "lint-paper-missing-problem"
    markdown = _short_semantically_complete_markdown(include_citation=False).replace(
        "problem statement",
        "setup note",
    ).replace("research problem", "research setting")
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "Problem statement is missing or not explicit." in payload[
        "quality_failure_reasons"
    ]


def test_lint_paper_bundle_fails_fake_empirical_claim(tmp_path) -> None:
    run_id = "lint-paper-fake-empirical"
    markdown = (
        _short_semantically_complete_markdown(include_citation=False)
        + "\nThe pipeline is empirically validated for real-world deployment.\n"
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "Fake empirical or real-world validation language is present." in payload[
        "quality_failure_reasons"
    ]


def test_lint_paper_bundle_fails_external_facts_without_citations(tmp_path) -> None:
    run_id = "lint-paper-uncited-fact"
    markdown = (
        _short_semantically_complete_markdown(include_citation=False)
        + "\nStudies show that this external setting is already established.\n"
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "External factual claims appear without citation markers." in payload[
        "quality_failure_reasons"
    ]


def test_lint_paper_bundle_passes_acceptable_synthetic_fixture(tmp_path) -> None:
    run_id = "lint-paper-pass"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["word_count"] >= payload["thresholds"]["min_words"]
    assert payload["average_words_per_section"] >= payload["thresholds"][
        "min_avg_words_per_section"
    ]
    assert payload["citation_marker_count"] >= 1
    assert payload["issues"] == []
    assert payload["warnings"] == []


def test_lint_paper_bundle_rejects_unregistered_citation_key(tmp_path) -> None:
    run_id = "lint-paper-unregistered-citation"
    markdown = _acceptable_markdown(include_citation=False).replace(
        "This introduction gives problem framing for the research problem",
        "This introduction gives problem framing for the research problem [@Invented2026]",
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["unregistered_citation_keys"] == ["Invented2026"]
    assert any("Unregistered citation keys" in reason for reason in payload["issues"])


def test_lint_paper_bundle_prefers_revised_artifact(tmp_path) -> None:
    run_id = "lint-paper-prefers-revised"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
        revised_markdown=_short_placeholder_markdown(),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["primary_artifact_to_read"].endswith(
        "reports/revised-manuscript-draft.md"
    )
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["title_is_placeholder"] is True
    assert payload["heading_fragmentation_detected"] is True


def test_lint_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_missing_optional_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "lint-paper-minimal"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["primary_artifact_to_read"].endswith(
        "reports/complete-manuscript-draft.md"
    )
    assert payload["paper_release_status"] is None
    assert payload["release_status_unchanged"] is True
    assert payload["safety_status_unchanged"] is True


def _prepare_paper_bundle(
    tmp_path,
    *,
    run_id: str,
    revised: bool,
    release: bool,
) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id=run_id, write_report=True),
        enable_safe_repair=revised,
    )
    if release:
        run_full_paper_release_gate(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
        )


def _run_file_snapshot(tmp_path, run_id: str) -> dict[str, str]:
    run_path = tmp_path / "runs" / run_id
    return {
        path.relative_to(run_path).as_posix(): sha256_file(path)
        for path in sorted(run_path.rglob("*"))
        if path.is_file()
    }


def _write_paper_bundle_markdown(
    tmp_path,
    *,
    run_id: str,
    complete_markdown: str,
    revised_markdown: str | None = None,
) -> None:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True)
    (reports / "complete-manuscript-draft.md").write_text(
        complete_markdown,
        encoding="utf-8",
    )
    if revised_markdown is not None:
        (reports / "revised-manuscript-draft.md").write_text(
            revised_markdown,
            encoding="utf-8",
        )


def _write_fixture_citation_registry(tmp_path, run_id: str) -> None:
    record = CitationRecord(
        citation_id="citation-fixture-smith",
        citation_key="Smith2024",
        source_id="fixture-smith",
        title="Fixture bounded context",
        authors=["Smith, Fixture"],
        year=2024,
        provider="fake",
        retrieval_backend="fake",
        retrieved_at="1970-01-01T00:00:00Z",
        raw_metadata_hash="0" * 64,
        source_type="test_fixture",
        allowed_citation_key="Smith2024",
        trust_level="fixture_only",
        source_status="fixture",
    )
    registry = CitationRegistry(
        run_id=run_id,
        citations=[record],
        bibliography=[
            BibliographyEntry(
                citation_id=record.citation_id,
                citation_key=record.citation_key,
                source_id=record.source_id,
                markdown="- [@Smith2024] Fixture metadata only.",
                has_source_provenance=True,
            )
        ],
        citation_key_policy="fixture",
        citation_policy="registry-only",
        retrieval_backend="fake",
        retrieval_scope="bounded-fixture",
        source_registry_hash="1" * 64,
        source_count=1,
        accepted_source_count=1,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "citation-registry.json").write_text(
        registry.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_fixture_retrieval_quality_report(tmp_path, run_id: str) -> None:
    report = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=3,
        accepted_source_count=1,
        rejected_source_count=2,
        duplicate_count=1,
        low_relevance_count=1,
        metadata_incomplete_count=0,
        mean_relevance_score=0.5,
        min_relevance_score=0.2,
        queries_used=["human geography bounded literature context"],
        coverage_limitations=[
            "Bounded local source set; not validation or publication readiness."
        ],
        adequacy_status="bounded_context_only",
        source_relevance_adjudication_enabled=True,
        source_relevance_adjudicator_backend="fake",
        source_relevance_adjudicator_model="test-model",
        source_relevance_adjudication_calls=1,
        adjudicated_source_count=2,
        deterministic_accept_count=0,
        deterministic_reject_count=0,
        llm_accepted_count=1,
        llm_rejected_count=1,
        hard_reject_count=1,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=["duplicate", "irrelevant"],
        rejection_reasons={
            "duplicate": "duplicate_source",
            "irrelevant": "low_relevance",
        },
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "retrieval-quality-report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_fixture_claim_support_audit(tmp_path, run_id: str) -> None:
    audit = ClaimSupportAuditReport(
        run_id=run_id,
        citation_registry_present=True,
        citation_policy="registry-only",
        claim_support_items=[
            ClaimSupportItem(
                sentence_id="introduction-p0-s0",
                section_name="Introduction and Problem Framing",
                sentence_text_hash="0" * 64,
                sentence_snippet="Citation registry records bounded context [@Smith2024].",
                claim_class="source_context_claim",
                citation_keys_present=["Smith2024"],
                required_support_type="registry_background_context",
                supporting_source_ids=["fixture-smith"],
                support_status="registry_supported",
            )
        ],
        summary_counts={
            "total_sentences": 1,
            "registry_supported": 1,
            "scaffold_not_required": 0,
            "missing_required_citation": 0,
            "scope_mismatch": 0,
            "forbidden_claim": 0,
            "citation_as_validation_misuse": 0,
        },
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "claim-support-audit.json").write_text(
        audit.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _short_placeholder_markdown() -> str:
    sections = "\n".join(
        f"## Section {index}\nPlaceholder text for section {index}."
        for index in range(1, 15)
    )
    return f"# Deterministic Branch Manuscript Plan\n\n{sections}\n"


def _acceptable_markdown(*, include_citation: bool) -> str:
    citation = " [@Smith2024]" if include_citation else ""
    sections = [
        ("Abstract", "This abstract summarizes the bounded argument"),
        (
            "Introduction",
            "This introduction gives problem framing for the research problem"
            f"{citation}",
        ),
        (
            "Central Contribution",
            "The central contribution of this draft is a bounded manuscript "
            "pipeline for human review only",
        ),
        ("Problem Framing", "The problem statement explains why the setting matters"),
        ("Method Summary", "The method summary describes the model and approach"),
        ("Results", "The results section reports only supported internal findings"),
        ("Limitations", "The limitations section keeps the scope bounded"),
        (
            "Claim/Evidence Appendix",
            "The claim/evidence appendix links claims to available artifacts",
        ),
        (
            "Provenance Appendix",
            "The provenance appendix records artifact and run context",
        ),
    ]
    body = "\n\n".join(
        f"## {heading}\n{_repeated_quality_paragraph(seed)}"
        for heading, seed in sections
    )
    return f"# Bounded Transport Calibration\n\n{body}\n"


def _repeated_quality_paragraph(seed: str) -> str:
    sentence = (
        f"{seed} while preserving evidence boundaries, citation safety, "
        "problem framing, method summary, limitations, claim evidence traceability, "
        "and provenance context for human review only."
    )
    return " ".join(sentence for _ in range(8))


def _short_semantically_complete_markdown(*, include_citation: bool) -> str:
    citation = " [@Smith2024]" if include_citation else ""
    return (
        "# Evidence-Bounded Manuscript Generation for Human Geography Research Candidates\n\n"
        "## Abstract\n"
        "The problem statement is to turn bounded research candidates into safe manuscript "
        "drafts. The central contribution of this draft is a deterministic manuscript package "
        "with evidence boundary checks. It is not proof evidence and does not provide "
        "empirical validation.\n\n"
        "## Introduction and Problem Framing\n"
        f"The research problem is manuscript usefulness under strict evidence limits{citation}. "
        "No retrieval-backed citations are invented.\n\n"
        "## Method and Model\n"
        "The method summary describes a pipeline that assembles approved section drafts, "
        "claim links, and audit context mechanically.\n\n"
        "## Claim and Evidence Boundaries\n"
        "The evidence boundary statement is that generated prose cannot create evidence, "
        "upgrade labels, or imply publication readiness.\n\n"
        "## Limitations\n"
        "Limitations include missing retrieval coverage, proof validation, experiment evidence, "
        "citation coverage, and human validation.\n\n"
        "## Claim/Evidence Appendix\n"
        "- `claim-main`: evidence artifacts are context only.\n\n"
        "## Provenance Appendix\n"
        "- Run ID: `run-1`; artifact and ledger audit context remain non-evidence.\n"
    )


def _short_seven_main_sections_with_appendices_markdown() -> str:
    return (
        "# Evidence-Bounded Manuscript Generation for Human Geography Research Candidates\n\n"
        "## Abstract\n"
        "This abstract states the research problem, central contribution, evidence boundary, "
        "and human-review-only status.\n\n"
        "## Introduction and Problem Framing\n"
        "The problem statement is how to keep manuscript generation useful while preserving "
        "strict evidence boundaries and avoiding publication readiness claims.\n\n"
        "## Method and Model\n"
        "The method summary describes deterministic planning, bounded drafting, safe repair, "
        "and audit reporting.\n\n"
        "## Claim and Evidence Boundaries\n"
        "The central contribution of this draft is a bounded paper package that keeps proof, "
        "experiment, citation, and provenance roles separate.\n\n"
        "## Demonstration Status\n"
        "The demonstration status records pipeline behavior only and is not proof evidence or "
        "empirical validation.\n\n"
        "## Limitations\n"
        "Limitations include missing real retrieval coverage, proof artifacts, experiment "
        "evidence, and human validation.\n\n"
        "## Conclusion\n"
        "The conclusion restates the bounded contribution and identifies future evidence work.\n\n"
        "## Claim/Evidence Appendix\n"
        "- Claims and evidence links remain audit context only.\n\n"
        "## Source/Citation Appendix\n"
        "- No registry-backed citation entries are asserted by this fixture.\n\n"
        "## Provenance Appendix\n"
        "- Run artifacts, reports, and ledger context remain non-evidence.\n"
    )
