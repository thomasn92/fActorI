from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import (
    build_autonomous_evidence_gap_plan,
    inspect_autonomous_evidence_gap_plan,
    persist_autonomous_evidence_gap_plan,
)
from factori.autonomous_loop import inspect_autonomous_loop, run_autonomous_loop
from factori.autonomous_plan_execution import (
    execute_autonomous_evidence_plan,
    inspect_autonomous_plan_execution,
)
from factori.claim_adjudication import FakeClaimAdjudicator
from factori.claim_evidence import (
    build_claim_evidence_map,
    inspect_claim_evidence_map,
    persist_claim_evidence_map,
)
from factori.cli import app
from factori.evidence_artifact_intake import (
    EvidenceArtifactIntakeError,
    ingest_experiment_artifact,
    ingest_proof_artifact,
    inspect_experiment_artifacts,
    inspect_proof_artifacts,
)
from factori.evidence_aware_refresh import (
    EvidenceAwareRefreshError,
    refresh_evidence_aware_manuscript,
)
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_reviewer_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.gap_attempts import (
    gap_fingerprint_for_plan_item,
    inspect_gap_attempt_history,
    inspect_planned_spec_dedup,
    planned_spec_fingerprint,
)
from factori.gap_strategy_diversification import (
    build_gap_strategy_diversification,
    inspect_gap_strategy_diversification,
    persist_gap_strategy_diversification,
    strategy_fingerprint,
    strategy_is_automation_ready,
)
from factori.hashing import sha256_file
from factori.human_review import (
    HumanReviewIntakeError,
    ingest_human_review,
    inspect_human_review,
)
from factori.human_review_reconciliation import (
    inspect_human_review_reconciliation,
    reconcile_human_review,
)
from factori.ledger import ResearchLedger
from factori.planned_spec_execution import (
    execute_planned_specs,
    inspect_planned_spec_execution,
)
from factori.reviewer_change_requests import (
    ReviewerChangeRequestError,
    ingest_reviewer_change_requests,
    inspect_reviewer_change_requests,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    AutonomousLoopIndex,
    AutonomousLoopRunReport,
    AutonomousPlanExecutionReport,
    CitationRecord,
    CitationRegistry,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    ControllerActionType,
    EvidenceAwareRefreshReport,
    ExperimentArtifact,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationStatus,
    FullPaperReleaseGateConfig,
    GapAttemptHistory,
    GapAttemptRecord,
    GapStrategyOption,
    GeneratedSectionDraft,
    HumanReviewArtifact,
    HumanReviewReconciliationIndex,
    HumanReviewReconciliationReport,
    PipelineRunConfig,
    PipelineStage,
    PlannedExperimentSpec,
    PlannedSpecDedupIndex,
    PlannedSpecDuplicateRecord,
    PlannedSpecExecutionReport,
    ProofArtifact,
    ProofObligationSpec,
    QualityRepairReport,
    RetrievalExpansionRequest,
    RetrievalQualityReport,
    ReviewerBundleSummary,
)


def test_full_paper_generation_models_are_importable() -> None:
    assert FullPaperGenerationConfig
    assert FullPaperArtifactBundle
    assert FullPaperGenerationReport
    assert QualityRepairReport
    assert ReviewerBundleSummary
    assert HumanReviewArtifact
    assert ProofArtifact
    assert ExperimentArtifact
    assert ClaimEvidenceMap
    assert ClaimEvidenceMapLink
    assert EvidenceAwareRefreshReport
    assert HumanReviewReconciliationReport
    assert AutonomousEvidenceGapPlan
    assert AutonomousPlanExecutionReport
    assert PlannedSpecExecutionReport
    assert AutonomousLoopRunReport
    assert AutonomousLoopIndex
    assert GapAttemptRecord
    assert GapAttemptHistory
    assert PlannedSpecDuplicateRecord
    assert PlannedSpecDedupIndex


def test_generate_full_paper_library_writes_expected_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-1")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = generate_full_paper(
        run_id="run-1",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id="run-1", write_report=True),
    )

    bundle = result.artifact_bundle
    assert result.report.generation_status in {
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED,
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert bundle.citation_registry_artifact_id == "citation-registry"
    assert bundle.complete_manuscript_draft_artifact_id == "complete-manuscript-draft"
    assert bundle.latex_artifact_id == "paper"
    assert bundle.latex_source_map_artifact_id == "latex-source-map"
    assert bundle.paper_critic_report_artifact_id == "paper-critic-report"
    assert bundle.full_paper_generation_report_artifact_id == "full-paper-generation-report"
    assert bundle.full_paper_artifact_bundle_artifact_id == "full-paper-artifact-bundle"
    assert bundle.claim_support_audit_artifact_id == "claim-support-audit"
    assert result.report.publication_ready is False
    assert result.report.is_verification_evidence is False
    for ref in (result.report_artifact, result.bundle_artifact):
        assert ref is not None
        _assert_non_evidence_artifact(tmp_path, ref)


def test_full_paper_generation_writes_fake_semantic_adjudication_audit(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-adjudicated")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-adjudicated/ledger.sqlite")

    generate_full_paper(
        run_id="run-adjudicated",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        claim_adjudicator=FakeClaimAdjudicator(),
        config=FullPaperGenerationConfig(run_id="run-adjudicated", write_report=True),
    )

    path = tmp_path / "runs/run-adjudicated/reports/claim-support-audit.json"
    audit = ClaimSupportAuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert audit.claim_adjudication_enabled is True
    assert audit.claim_adjudicator_backend == "fake"
    assert audit.claim_adjudication_calls >= 1
    assert audit.creates_scientific_validation is False
    assert audit.implies_publication_readiness is False


def test_generate_paper_cli_works_and_json_is_valid(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-json")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-json",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    generated = payload["full_paper_generation_result"]
    assert generated["is_verification_evidence"] is False
    assert generated["report"]["publication_ready"] is False
    bundle = generated["artifact_bundle"]
    assert bundle["complete_manuscript_draft_artifact_id"] == "complete-manuscript-draft"
    assert bundle["latex_artifact_id"] == "paper"
    assert bundle["paper_critic_report_artifact_id"] == "paper-critic-report"


def test_generate_paper_write_report_writes_full_report_and_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-report")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-report",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    report_ref = ArtifactRef.model_validate(artifacts["full_paper_generation_report"])
    bundle_ref = ArtifactRef.model_validate(artifacts["full_paper_artifact_bundle"])
    _assert_non_evidence_artifact(tmp_path, report_ref)
    _assert_non_evidence_artifact(tmp_path, bundle_ref)
    assert (tmp_path / "runs" / "run-report" / "reports" / "citation-registry.json").is_file()
    assert (tmp_path / "runs" / "run-report" / "reports" / "complete-manuscript-draft.md").is_file()
    assert (tmp_path / "runs" / "run-report" / "latex" / "paper.tex").is_file()
    assert (tmp_path / "runs" / "run-report" / "reports" / "paper-critic-report.json").is_file()
    claim_support_path = tmp_path / "runs" / "run-report" / "reports" / "claim-support-audit.json"
    assert claim_support_path.is_file()
    claim_support = json.loads(claim_support_path.read_text(encoding="utf-8"))
    assert claim_support["creates_scientific_validation"] is False


def test_generate_paper_without_revision_does_not_write_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-no-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-no-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (
        tmp_path / "runs" / "run-no-revision" / "reports" / "revised-manuscript-draft.md"
    ).exists()


def test_generate_paper_with_safe_fake_revision_writes_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-revision",
            "--apply-safe-fake-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_manuscript_draft_artifact_id"] == "revised-manuscript-draft"
    revised = tmp_path / "runs" / "run-revision" / "reports" / "revised-manuscript-draft.md"
    assert revised.is_file()
    linked = ArtifactRef.model_validate_json(
        (tmp_path / "runs/run-revision/reports/revised-manuscript-draft.md.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert "publication ready" not in revised.read_text(encoding="utf-8").lower()


def test_generate_paper_reexports_latex_after_revision(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-reexport")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-reexport",
            "--apply-safe-fake-revision",
            "--reexport-latex-after-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    revised_ref = ArtifactRef.model_validate(artifacts["revised_paper"])
    assert revised_ref.path.endswith("latex/revised-paper.tex")
    _assert_non_evidence_artifact(tmp_path, revised_ref)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_latex_artifact_id"] == "revised-paper"
    assert bundle["revised_latex_source_map_artifact_id"] == "revised-latex-source-map"


def test_safe_repair_writes_hashed_non_evidence_audit_artifact(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-safe-repair")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-safe-repair/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-safe-repair",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-safe-repair",
            write_report=True,
        ),
        enable_safe_repair=True,
    )

    assert result.revision_result is not None
    repair_ref = result.revision_result.safe_repair_report_artifact
    assert repair_ref is not None
    _assert_non_evidence_artifact(tmp_path, repair_ref)
    payload = json.loads((tmp_path / repair_ref.path).read_text(encoding="utf-8"))
    assert payload["before_content_hash"]
    assert payload["after_content_hash"]
    assert payload["invented_citations"] is False
    assert payload["created_or_upgraded_labels"] is False
    assert payload["source_aware_missing_citation_repairs_attempted"] >= 0
    assert payload["source_aware_citations_added"] >= 0
    assert payload["source_aware_claims_downgraded"] >= 0
    assert payload["source_aware_claims_removed"] >= 0
    assert payload["source_aware_repairs_unresolved"] >= 0
    assert payload["source_aware_repair_used_rejected_source"] is False
    assert payload["source_aware_repair_used_hard_rejected_source"] is False
    assert payload["citation_required_items_adjudicated_or_repaired"] is True
    assert payload["creates_scientific_validation"] is False
    assert payload["implies_publication_readiness"] is False
    assert payload["is_verification_evidence"] is False
    assert result.artifact_bundle.revised_manuscript_draft_artifact_id == (
        "revised-manuscript-draft"
    )
    assert result.artifact_bundle.revised_latex_artifact_id == "revised-paper"
    revised_markdown = (
        tmp_path / "runs/run-safe-repair/reports/revised-manuscript-draft.md"
    ).read_text(encoding="utf-8")
    assert "## Central Message" not in revised_markdown
    assert "**Central message.**" in revised_markdown
    lint = lint_paper_bundle_summary(run_id="run-safe-repair", root=tmp_path)
    assert lint["main_body_section_count"] == 7
    assert lint["appendix_section_count"] == 2
    assert lint["standalone_central_message_detected"] is False
    assert lint["central_message_merged"] is True


def test_deterministic_quality_repair_writes_safe_report_and_revised_draft(
    tmp_path,
) -> None:
    _prepare_run(tmp_path, run_id="run-quality-repair")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-quality-repair/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-quality-repair",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-quality-repair",
            write_report=True,
            quality_repair_backend="deterministic",
            quality_repair_model="unused",
        ),
        enable_safe_repair=True,
    )

    assert result.quality_repair_report_artifact is not None
    _assert_non_evidence_artifact(tmp_path, result.quality_repair_report_artifact)
    report_path = tmp_path / result.quality_repair_report_artifact.path
    report = QualityRepairReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.quality_repair_enabled is True
    assert report.quality_repair_backend == "deterministic"
    assert report.quality_repair_status in {"repaired", "no_action_needed"}
    assert report.claim_support_rechecked_after_repair is True
    assert report.citation_safety_rechecked_after_repair is True
    assert report.section_depth_targets["Abstract"]["min_words"] == 130
    assert report.sections_below_target_after == []
    assert report.placeholder_like_sections_after == []
    assert report.warnings_reduced_count >= 1
    assert "Draft may be skeletal: below proxy word-count target." in (
        report.quality_warnings_before
    )
    assert "Draft may be skeletal: below proxy word-count target." not in (
        report.quality_warnings_after
    )
    for heading, target in report.section_depth_targets.items():
        assert report.section_word_counts_after[heading] >= target["min_words"]
    assert report.creates_scientific_validation is False
    assert report.implies_publication_readiness is False
    assert report.is_verification_evidence is False
    assert result.artifact_bundle.quality_repair_report_artifact_id == ("quality-repair-report")
    assert result.artifact_bundle.revised_manuscript_draft_artifact_id == (
        "revised-manuscript-draft"
    )
    revised = (tmp_path / "runs/run-quality-repair/reports/revised-manuscript-draft.md").read_text(
        encoding="utf-8"
    )
    lowered = revised.lower()
    assert "publication_ready=false" in revised
    assert "publication ready" not in lowered
    assert "empirically validated" not in lowered
    assert "source relevance and retrieval adequacy remain non-evidential" in lowered
    assert "accepted_source_count" in revised
    assert "absence of proof artifacts" in lowered
    assert "absence of experiment artifacts" in lowered
    lint = lint_paper_bundle_summary(run_id="run-quality-repair", root=tmp_path)
    assert lint["quality_repair_report_present"] is True
    assert lint["quality_repair_backend"] == "deterministic"
    assert lint["quality_repaired_section_count"] >= 1
    assert lint["section_depth_targets_present"] is True
    assert lint["sections_below_depth_target"] == []
    assert lint["placeholder_sections_after_quality_repair"] == []
    assert lint["warnings_reduced_count"] >= 1
    assert lint["limitations_concrete_constraint_count"] >= 2
    assert lint["claim_support_rechecked_after_quality_repair"] is True
    assert lint["citation_safety_rechecked_after_quality_repair"] is True
    assert lint["claim_support_forbidden_claim_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["unregistered_citation_keys"] == []
    assert lint["publication_ready"] is False


def test_reviewer_bundle_summary_is_written_after_release(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-reviewer-summary")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-reviewer-summary/ledger.sqlite")

    generate_full_paper(
        run_id="run-reviewer-summary",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-reviewer-summary",
            write_report=True,
            quality_repair_backend="deterministic",
        ),
        enable_safe_repair=True,
    )
    release = run_full_paper_release_gate(
        run_id="run-reviewer-summary",
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(
            run_id="run-reviewer-summary",
            write_report=True,
        ),
    )

    json_path = tmp_path / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.json"
    markdown_path = tmp_path / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.md"
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert release.reviewer_summary_artifact is not None
    assert release.reviewer_summary_markdown_artifact is not None
    _assert_non_evidence_artifact(tmp_path, release.reviewer_summary_artifact)
    _assert_non_evidence_artifact(
        tmp_path,
        release.reviewer_summary_markdown_artifact,
    )

    summary = ReviewerBundleSummary.model_validate_json(json_path.read_text(encoding="utf-8"))
    inspected = inspect_reviewer_bundle_summary(
        run_id="run-reviewer-summary",
        root=tmp_path,
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    lowered = markdown.casefold()

    assert summary.release_status == release.report.decision.status.value
    assert summary.publication_ready is False
    assert summary.claim_support_status == "clean"
    assert summary.citation_status in {"registry-backed", "no-citations-required"}
    assert summary.retrieval_quality_status
    assert summary.source_relevance_status
    assert summary.quality_repair_status in {"repaired", "no_action_needed"}
    assert summary.creates_scientific_validation is False
    assert summary.implies_publication_readiness is False
    assert summary.is_verification_evidence is False
    assert any("No proof artifact" in gap for gap in summary.evidence_gaps)
    assert any("No experiment artifact" in gap for gap in summary.evidence_gaps)
    assert any("No human-review artifact" in gap for gap in summary.evidence_gaps)
    assert len(summary.human_review_checklist) > 0
    assert len(summary.recommended_next_actions) > 0
    assert inspected["release_status"] == summary.release_status
    assert inspected["publication_ready"] is False
    assert inspected["summary_path"].endswith("reviewer-bundle-summary.json")
    assert inspected["markdown_summary_path"].endswith("reviewer-bundle-summary.md")
    for phrase in (
        "scientifically validated",
        "validated result",
        "proves novelty",
        "establishes correctness",
        "ready to submit",
        "ready for publication",
        "approved",
    ):
        assert phrase not in lowered


def test_valid_human_review_artifact_is_ingested_and_updates_summary(tmp_path) -> None:
    run_id = "run-human-review"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)

    result = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )

    assert result.review.review_status == "reviewed_ready_for_evidence_generation"
    assert result.review.creates_scientific_validation is False
    assert result.review.implies_publication_readiness is False
    assert result.review.is_verification_evidence is False
    assert result.persistence.commit.action_type == ControllerActionType.HUMAN_REVIEW_INGESTED
    _assert_non_evidence_artifact(tmp_path, result.review_artifact)
    _assert_non_evidence_artifact(tmp_path, result.review_summary_artifact)
    _assert_non_evidence_artifact(tmp_path, result.reviewer_summary_artifact)

    inspected_review = inspect_human_review(run_id=run_id, root=tmp_path)
    assert inspected_review["human_review_artifact_present"] is True
    assert inspected_review["publication_ready"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["summary_path"].endswith("reviewer-bundle-summary-after-human-review.json")
    assert summary["human_review_artifact_present"] is True
    assert summary["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert summary["human_review_blocking_concern_count"] == 0
    assert summary["human_review_requested_change_count"] == 0
    assert summary["publication_ready"] is False
    assert not any("No human-review artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No proof artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No experiment artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_artifact_present"] is True
    assert lint["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert lint["publication_ready"] is False


def test_blocking_human_review_concerns_are_surfaced(tmp_path) -> None:
    run_id = "run-human-review-blocking"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        review_status="reviewed_with_blocking_changes",
        blocking_concerns=["Problem framing needs human revision."],
        requested_changes=["Revise problem framing before evidence generation."],
        recommended_next_action="Address blocking human-review concerns first.",
    )

    ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["human_review_blocking_concern_count"] == 1
    assert summary["human_review_requested_change_count"] == 1
    assert "Problem framing needs human revision." in summary["blocking_issues"]
    assert any(
        "blocking human-review concerns" in action for action in summary["recommended_next_actions"]
    )


def test_human_review_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id="different-run",
        reviewed_run_id=run_id,
    )

    with pytest.raises(HumanReviewIntakeError, match="run_id does not match"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_missing_checklist_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-missing-checklist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        checklist_items=[],
    )

    with pytest.raises(HumanReviewIntakeError, match="Invalid human review artifact"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_missing_attestation_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-missing-attestation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        reviewer_attestation="   ",
    )

    with pytest.raises(HumanReviewIntakeError, match="attestation is required"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        non_blocking_comments=["This draft is ready for publication."],
    )

    with pytest.raises(HumanReviewIntakeError, match="forbidden publication"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


@pytest.mark.parametrize(
    "unsafe_claim",
    [
        "The proof is verified.",
        "The experiment is validated.",
        "Novelty confirmed.",
        "Correctness is established.",
    ],
)
def test_human_review_validation_authority_claims_are_rejected(
    tmp_path,
    unsafe_claim: str,
) -> None:
    run_id = "run-human-review-validation-claims"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        non_blocking_comments=[unsafe_claim],
        filename=f"{unsafe_claim.casefold().replace(' ', '-')}.json",
    )

    with pytest.raises(HumanReviewIntakeError, match="forbidden publication"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_valid_formal_proof_artifact_is_ingested_and_removes_proof_gap(tmp_path) -> None:
    run_id = "run-proof-formal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id)

    result = ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    assert result.persistence.commit.action_type == ControllerActionType.PROOF_ARTIFACT_INGESTED
    assert result.proof.is_verification_evidence is True
    _assert_artifact_boundary_flags(
        tmp_path,
        result.proof_artifact,
        is_verification_evidence=True,
    )
    _assert_artifact_boundary_flags(tmp_path, result.proof_index_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.reviewer_summary_artifact)

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 1
    assert inspected["proof_evidence_gap_present"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["proof_artifact_count"] == 1
    assert summary["formal_verification_artifact_count"] == 1
    assert summary["publication_ready"] is False
    assert not any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["proof_artifact_count"] == 1
    assert lint["formal_verification_passed_count"] == 1
    assert lint["proof_evidence_gap_present"] is False
    assert lint["publication_ready"] is False


def test_informal_proof_note_is_ingested_without_formal_verification(tmp_path) -> None:
    run_id = "run-proof-informal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_id="informal-proof-note-001",
        proof_type="informal_proof_note",
        checker_status="not_checked",
        is_verification_evidence=False,
        proof_hash="3333333333333333333333333333333333333333333333333333333333333333",
    )

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["informal_proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 0
    assert inspected["proof_evidence_gap_present"] is True


def test_failed_proof_check_is_ingested_without_removing_proof_gap(tmp_path) -> None:
    run_id = "run-proof-failed"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_id="failed-proof-check-001",
        checker_status="failed",
        is_verification_evidence=False,
        proof_hash="4444444444444444444444444444444444444444444444444444444444444444",
    )

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["failed_or_inconclusive_proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 0
    assert inspected["proof_evidence_gap_present"] is True


def test_proof_artifact_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-proof-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id="different-run",
        artifact_run_id=run_id,
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="run_id does not match"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_proof_artifact_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-proof-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        statement="This fixture wrongly says the bundle is publication ready.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_proof_artifact_formal_verification_without_passed_checker_is_rejected(
    tmp_path,
) -> None:
    run_id = "run-proof-bad-formal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        checker_status="failed",
        is_verification_evidence=True,
        statement="This fixture wrongly says proof verified despite a failed checker.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="verification-evidence flag"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_llm_generated_formal_proof_artifact_is_rejected(tmp_path) -> None:
    run_id = "run-proof-llm-generated"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        statement="As an AI language model, I generated this formal proof text.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="LLM-generated proof"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_completed_experiment_artifact_is_ingested_and_removes_experiment_gap(
    tmp_path,
) -> None:
    run_id = "run-experiment-completed"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(tmp_path, run_id=run_id)

    result = ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.EXPERIMENT_ARTIFACT_INGESTED
    )
    _assert_artifact_boundary_flags(tmp_path, result.experiment_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.experiment_index_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.reviewer_summary_artifact)

    inspected = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["experiment_artifact_count"] == 1
    assert inspected["completed_experiment_count"] == 1
    assert inspected["experiment_evidence_gap_present"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["experiment_artifact_count"] == 1
    assert summary["completed_experiment_count"] == 1
    assert not any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["experiment_artifact_count"] == 1
    assert lint["completed_experiment_count"] == 1
    assert lint["experiment_evidence_gap_present"] is False
    assert lint["publication_ready"] is False


@pytest.mark.parametrize("status", ["inconclusive", "failed"])
def test_non_completed_experiment_artifact_does_not_remove_experiment_gap(
    tmp_path,
    status: str,
) -> None:
    run_id = f"run-experiment-{status}"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        experiment_id=f"{status}-experiment-001",
        status=status,
        config_hash=(
            "8888888888888888888888888888888888888888888888888888888888888888"
            if status == "inconclusive"
            else "9999999999999999999999999999999999999999999999999999999999999999"
        ),
    )

    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    inspected = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["experiment_artifact_count"] == 1
    assert inspected["completed_experiment_count"] == 0
    assert inspected["experiment_evidence_gap_present"] is True


def test_experiment_artifact_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id="different-run",
        artifact_run_id=run_id,
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="run_id does not match"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_experiment_artifact_broad_validation_claim_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-broad-validation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        result_summary="This fixture wrongly says the experiment validated the paper.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_experiment_artifact_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        result_summary="This fixture wrongly says the bundle is publication ready.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_proof_and_experiment_artifacts_update_reviewer_summary_together(
    tmp_path,
) -> None:
    run_id = "run-proof-experiment-summary"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id)
    experiment_file = _write_experiment_artifact_fixture(tmp_path, run_id=run_id)

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["proof_artifact_count"] == 1
    assert summary["formal_verification_artifact_count"] == 1
    assert summary["experiment_artifact_count"] == 1
    assert summary["completed_experiment_count"] == 1
    assert summary["publication_ready"] is False
    assert not any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])
    assert not any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No human-review artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["proof_evidence_gap_present"] is False
    assert lint["experiment_evidence_gap_present"] is False
    assert lint["remaining_evidence_gap_count"] == 1


def test_claim_evidence_map_is_persisted_and_summarized(tmp_path) -> None:
    run_id = "run-claim-evidence-persist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.CLAIM_EVIDENCE_MAP_WRITTEN
    )
    assert result.claim_evidence_map.publication_ready is False
    assert result.claim_evidence_map.creates_scientific_validation is False
    assert (tmp_path / result.map_artifact.path).is_file()
    assert (tmp_path / result.markdown_artifact.path).is_file()
    inspected = inspect_claim_evidence_map(run_id=run_id, root=tmp_path)
    assert inspected["claim_evidence_map_present"] is True
    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["claim_evidence_map_present"] is True
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["claim_evidence_map_present"] is True
    assert lint["publication_ready"] is False


def test_claim_evidence_map_links_citation_supported_background_claim(tmp_path) -> None:
    run_id = "run-claim-evidence-citation"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="background-claim-1",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(run_id=run_id),
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == "supported_within_scope"
    assert link.support_type == "citation_background_context"
    assert link.supporting_source_ids == ["source-1"]
    assert claim_map.summary_counts["citation_supported_background_claim"] == 1


@pytest.mark.parametrize(
    ("source_status", "rejected_source_ids", "rejection_reasons"),
    [
        ("rejected", ["source-1"], {"source-1": "deterministic reject"}),
        ("retrieved", ["source-1"], {"source-1": "hard metadata reject"}),
    ],
)
def test_claim_evidence_map_rejected_sources_cannot_support_claims(
    tmp_path,
    source_status: str,
    rejected_source_ids: list[str],
    rejection_reasons: dict[str, str],
) -> None:
    run_id = "run-claim-evidence-rejected-source"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="background-claim-1",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
        citation_registry=_citation_registry_fixture(
            run_id=run_id,
            source_status=source_status,
            accepted_for_registry=source_status != "rejected",
        ),
        retrieval_quality=_retrieval_quality_fixture(
            run_id=run_id,
            accepted_source_ids=[],
            rejected_source_ids=rejected_source_ids,
            rejection_reasons=rejection_reasons,
        ),
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status in {"partially_supported", "unsupported"}
    assert link.support_type == "unsupported"
    assert claim_map.summary_counts["citation_supported_background_claim"] == 0


@pytest.mark.parametrize(
    (
        "proof_type",
        "checker_status",
        "is_verification_evidence",
        "expected_status",
        "expected_type",
    ),
    [
        (
            "lean_verified",
            "passed",
            True,
            "supported_within_scope",
            "formal_proof_verification",
        ),
        (
            "informal_proof_note",
            "not_checked",
            False,
            "partially_supported",
            "informal_proof_context",
        ),
        ("lean_verified", "failed", False, "unsupported", "unsupported"),
    ],
)
def test_claim_evidence_map_links_proof_artifacts_by_authority(
    tmp_path,
    proof_type: str,
    checker_status: str,
    is_verification_evidence: bool,
    expected_status: str,
    expected_type: str,
) -> None:
    run_id = f"run-claim-evidence-proof-{checker_status}"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_proof_artifact_report(
        tmp_path,
        run_id=run_id,
        proof_id=f"proof-{checker_status}",
        proof_type=proof_type,
        claim_ids_or_statement_ids=["proof-claim-1"],
        checker_status=checker_status,
        is_verification_evidence=is_verification_evidence,
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == expected_status
    assert link.support_type == expected_type
    if expected_type == "formal_proof_verification":
        assert claim_map.summary_counts["proof_supported_claim"] == 1


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("completed", "supported_within_scope"),
        ("inconclusive", "unsupported"),
        ("failed", "unsupported"),
    ],
)
def test_claim_evidence_map_links_completed_experiments_only(
    tmp_path,
    status: str,
    expected_status: str,
) -> None:
    run_id = f"run-claim-evidence-experiment-{status}"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="experiment-claim-1",
                claim_class="experiment_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_experiment_artifact_report(
        tmp_path,
        run_id=run_id,
        experiment_id=f"experiment-{status}",
        claim_ids_or_section_ids=["experiment-claim-1"],
        status=status,
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == expected_status
    if status == "completed":
        assert link.support_type == "experiment_result"
        assert claim_map.summary_counts["experiment_supported_claim"] == 1
    else:
        assert link.support_type == "unsupported"


def test_claim_evidence_map_does_not_let_experiment_support_proof_claim(
    tmp_path,
) -> None:
    run_id = "run-claim-evidence-experiment-no-proof"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_experiment_artifact_report(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["proof-claim-1"],
        status="completed",
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    assert claim_map.links[0].support_status == "unsupported"
    assert claim_map.summary_counts["experiment_supported_claim"] == 0


def test_claim_evidence_map_blocks_proof_for_novelty_or_readiness_claim(
    tmp_path,
) -> None:
    run_id = "run-claim-evidence-proof-no-novelty"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="novelty-claim-1",
                claim_class="novelty_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_proof_artifact_report(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=["novelty-claim-1"],
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == "blocked_forbidden_claim"
    assert link.supporting_proof_artifact_ids == []
    assert claim_map.publication_ready is False


def test_autonomous_evidence_plan_is_persisted_and_exposed(tmp_path) -> None:
    run_id = "run-autonomous-plan-persist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.AUTONOMOUS_EVIDENCE_PLAN_WRITTEN
    )
    assert result.plan.planner_backend == "deterministic"
    assert result.plan.planner_status == "planned"
    assert result.plan.plan_items
    assert result.plan.publication_ready is False
    assert result.plan.creates_scientific_validation is False
    assert result.plan.implies_publication_readiness is False
    assert result.plan.is_verification_evidence is False
    _assert_non_evidence_artifact(tmp_path, result.plan_artifact)
    _assert_non_evidence_artifact(tmp_path, result.markdown_artifact)
    inspected = inspect_autonomous_evidence_gap_plan(run_id=run_id, root=tmp_path)
    assert inspected["autonomous_evidence_plan_present"] is True
    assert inspected["autonomous_plan_item_count"] == len(result.plan.plan_items)
    assert inspected["autonomous_human_intervention_required"] is False

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["summary_path"].endswith(
        "reviewer-bundle-summary-after-autonomous-evidence-plan-0001.json"
    )
    assert reviewer["autonomous_evidence_plan_present"] is True
    assert reviewer["automation_ready_item_count"] >= 0
    assert reviewer["human_intervention_required"] is False
    assert reviewer["publication_ready"] is False

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["autonomous_evidence_plan_present"] is True
    assert lint["autonomous_plan_item_count"] == len(result.plan.plan_items)
    assert lint["autonomous_human_intervention_required"] is False
    assert lint["publication_ready"] is False


def test_autonomous_evidence_plan_classifies_claim_gaps(tmp_path) -> None:
    run_id = "run-autonomous-plan-gaps"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(run_id=run_id),
        items=[
            _claim_support_item(
                sentence_id="empirical-claim",
                claim_class="experiment_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="theorem-claim",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="background-claim",
                claim_class="literature_background_claim",
                support_status="missing_required_citation",
            ),
            _claim_support_item(
                sentence_id="novelty-claim",
                claim_class="novelty_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="supported-background-claim",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            ),
        ],
    )
    _write_claim_evidence_map_report(
        tmp_path,
        build_claim_evidence_map(run_id=run_id, root=tmp_path),
    )

    plan = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    by_claim = {item.target_claim_id_optional: item for item in plan.plan_items}
    assert by_claim["empirical-claim"].gap_type == "needs_python_experiment"
    assert by_claim["theorem-claim"].gap_type == "needs_formal_proof"
    assert by_claim["background-claim"].gap_type == "needs_retrieval_expansion"
    assert by_claim["novelty-claim"].gap_type == "needs_claim_removal"
    assert by_claim["supported-background-claim"].gap_type == (
        "sufficiently_supported_for_bounded_draft"
    )
    assert plan.ready_for_python_experiment_runner is True
    assert plan.ready_for_formal_proof_attempt is True
    assert plan.ready_for_retrieval_expansion is True
    assert plan.requires_human_intervention is False
    assert plan.creates_scientific_validation is False
    assert plan.implies_publication_readiness is False
    assert plan.is_verification_evidence is False


def test_autonomous_evidence_plan_treats_bounded_retrieval_as_nonblocking(
    tmp_path,
) -> None:
    run_id = "run-autonomous-plan-bounded-retrieval"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(
            run_id=run_id,
            accepted_source_ids=["source-1"],
            rejected_source_ids=["source-2"],
            rejection_reasons={"source-2": "deterministic reject"},
        ),
        items=[
            _claim_support_item(
                sentence_id="supported-background-claim",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
    )
    _write_claim_evidence_map_report(
        tmp_path,
        build_claim_evidence_map(run_id=run_id, root=tmp_path),
    )

    plan = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    retrieval_items = [item for item in plan.plan_items if item.target_type == "retrieval"]
    assert retrieval_items
    assert retrieval_items[0].gap_type == "needs_retrieval_expansion"
    assert retrieval_items[0].blocking is False
    assert retrieval_items[0].automation_ready is True
    assert plan.requires_human_intervention is False


def test_autonomous_evidence_plan_requires_human_for_missing_or_corrupt_map(
    tmp_path,
) -> None:
    run_id = "run-autonomous-plan-missing-map"
    (tmp_path / "runs" / run_id / "reports").mkdir(parents=True)

    missing = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert missing.planner_status == "blocked_missing_claim_evidence_map"
    assert missing.requires_human_intervention is True
    assert missing.plan_items == []

    corrupt_run_id = "run-autonomous-plan-corrupt-map"
    reports = tmp_path / "runs" / corrupt_run_id / "reports"
    reports.mkdir(parents=True)
    (reports / "claim-evidence-map.json").write_text("{not-json}\n", encoding="utf-8")

    corrupt = build_autonomous_evidence_gap_plan(
        run_id=corrupt_run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert corrupt.planner_status == "blocked_corrupt_claim_evidence_map"
    assert corrupt.requires_human_intervention is True
    assert corrupt.human_intervention_reason_optional


def test_autonomous_plan_executor_dry_run_and_apply_are_bounded(tmp_path) -> None:
    run_id = "run-autonomous-executor"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    manuscript = tmp_path / "runs" / run_id / "reports" / "revised-manuscript-draft.md"
    claim_map = tmp_path / "runs" / run_id / "reports" / "claim-evidence-map.json"
    manuscript_hash = sha256_file(manuscript)
    claim_map_hash = sha256_file(claim_map)

    dry_run = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="dry-run",
        executor_backend="deterministic",
    )

    assert dry_run.report.execution_status == "dry_run_completed"
    assert dry_run.report.manuscript_modified is False
    assert dry_run.report.claim_evidence_map_rebuilt is False
    assert sha256_file(manuscript) == manuscript_hash
    assert sha256_file(claim_map) == claim_map_hash
    assert not list((tmp_path / "runs" / run_id / "reports").glob("proof-obligation-spec-*.json"))
    assert not list((tmp_path / "runs" / run_id / "reports").glob("experiment-spec-*.json"))

    applied = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    inspected = inspect_autonomous_plan_execution(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert applied.report.execution_status in {
        "completed",
        "completed_with_deferred_actions",
    }
    assert applied.report.claim_support_rechecked is True
    assert applied.report.citation_safety_rechecked is True
    assert applied.report.claim_evidence_map_rebuilt is True
    assert applied.report.release_rechecked is True
    assert applied.report.publication_ready is False
    assert applied.report.creates_scientific_validation is False
    assert inspected["autonomous_execution_count"] == 2
    assert inspected["latest_autonomous_execution_mode"] == "apply"
    assert lint["autonomous_execution_present"] is True
    assert lint["autonomous_execution_count"] == 2
    assert lint["publication_ready"] is False
    assert not list((tmp_path / "runs" / run_id / "reports").glob("proof-artifact-*.json"))
    assert not list((tmp_path / "runs" / run_id / "reports").glob("experiment-artifact-*.json"))


def test_autonomous_plan_executor_applies_safe_text_and_creates_planned_specs(
    tmp_path,
) -> None:
    run_id = "run-autonomous-executor-actions"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    audit = ClaimSupportAuditReport.model_validate_json(
        (reports / "claim-support-audit.json").read_text(encoding="utf-8")
    )
    targets = [
        item
        for item in audit.claim_support_items
        if item.section_name not in {"Bibliography", "References"} and item.sentence_snippet
    ][:2]
    assert len(targets) == 2
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=map_result.map_artifact.path,
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-downgrade",
                target_type="claim",
                target_claim_id_optional=targets[0].sentence_id,
                target_section_optional=targets[0].section_name,
                current_support_status="unsupported",
                gap_type="needs_claim_downgrade",
                recommended_action="Downgrade to bounded scaffold wording.",
                priority="high",
                blocking=True,
                rationale="Fixture unsupported broad claim.",
                expected_artifact_type="revised_manuscript",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-removal",
                target_type="claim",
                target_claim_id_optional=targets[1].sentence_id,
                target_section_optional=targets[1].section_name,
                current_support_status="blocked_forbidden_claim",
                gap_type="needs_claim_removal",
                recommended_action="Remove forbidden unsupported wording.",
                priority="blocking",
                blocking=True,
                rationale="Fixture forbidden authority claim.",
                expected_artifact_type="revised_manuscript",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-experiment",
                target_type="claim",
                target_claim_id_optional="fixture-empirical-claim",
                target_section_optional="Demonstration Status",
                current_support_status="unsupported",
                gap_type="needs_python_experiment",
                recommended_action="Plan a bounded local experiment.",
                priority="high",
                blocking=True,
                rationale="Fixture empirical result requires an experiment.",
                expected_artifact_type="experiment_artifact",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-proof",
                target_type="claim",
                target_claim_id_optional="fixture-theorem-claim",
                target_section_optional="Method and Model",
                current_support_status="unsupported",
                gap_type="needs_formal_proof",
                recommended_action="Plan a scoped formal proof attempt.",
                priority="high",
                blocking=True,
                rationale="Fixture theorem requires a passed checker.",
                expected_artifact_type="proof_artifact",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-retrieval",
                target_type="retrieval",
                current_support_status="bounded_context_only",
                gap_type="needs_retrieval_expansion",
                recommended_action="Plan bounded retrieval expansion.",
                priority="low",
                blocking=False,
                rationale="Fixture retrieval remains bounded.",
                expected_artifact_type="retrieval_quality_report",
                automation_ready=True,
            ),
        ],
        ready_for_python_experiment_runner=True,
        ready_for_formal_proof_attempt=True,
        ready_for_retrieval_expansion=True,
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )

    assert result.report.manuscript_modified is True
    assert result.report.actions_applied == 5
    experiment_specs = [
        path
        for path in reports.glob("experiment-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    proof_specs = [
        path
        for path in reports.glob("proof-obligation-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    retrieval_specs = [
        path
        for path in reports.glob("retrieval-expansion-request-*.json")
        if not path.name.endswith(".meta.json")
    ]
    assert len(experiment_specs) == len(proof_specs) == len(retrieval_specs) == 1
    experiment_spec = json.loads(experiment_specs[0].read_text(encoding="utf-8"))
    proof_spec = json.loads(proof_specs[0].read_text(encoding="utf-8"))
    retrieval_spec = json.loads(retrieval_specs[0].read_text(encoding="utf-8"))
    assert experiment_spec["status"] == "planned"
    assert experiment_spec["is_verification_evidence"] is False
    assert proof_spec["status"] == "planned"
    assert proof_spec["is_verification_evidence"] is False
    assert retrieval_spec["status"] == "planned"
    assert retrieval_spec["is_verification_evidence"] is False
    assert not list(reports.glob("proof-artifact-*.json"))
    assert not list(reports.glob("experiment-artifact-*.json"))


def test_gap_and_spec_fingerprints_are_stable() -> None:
    item_a = AutonomousEvidenceGapPlanItem(
        item_id="plan-item-001",
        target_type="claim",
        target_claim_id_optional="claim-1",
        target_section_optional="Method and Model",
        current_support_status="unsupported",
        gap_type="needs_formal_proof",
        recommended_action="Schedule a scoped formal proof attempt.",
        priority="high",
        blocking=True,
        rationale="Theorem-like claim needs proof.",
        required_inputs=["claim_id=claim-1", "claim_text_hash=" + "a" * 64],
        expected_artifact_type="proof_artifact",
        automation_ready=True,
    )
    item_b = item_a.model_copy(update={"item_id": "plan-item-999"})
    assert gap_fingerprint_for_plan_item(run_id="run-1", item=item_a) == (
        gap_fingerprint_for_plan_item(run_id="run-1", item=item_b)
    )

    spec_a = ProofObligationSpec(
        run_id="run-1",
        spec_id="proof-obligation-spec-a",
        target_claim_id="claim-1",
        statement="A scoped statement requires proof evidence.",
        suggested_checker="explicitly configured local formal proof backend",
        required_artifact_type="passed scoped proof artifact",
    )
    spec_b = spec_a.model_copy(update={"spec_id": "proof-obligation-spec-b"})
    assert planned_spec_fingerprint(spec_a) == planned_spec_fingerprint(spec_b)


def test_strategy_fingerprint_is_stable() -> None:
    inputs = {
        "gap_fingerprint": "a" * 64,
        "target_claim_id_optional": "claim-1",
        "target_section_optional": "Method and Model",
        "gap_type": "needs_formal_proof",
        "alternative_action": "Split the statement into scoped subclaims.",
        "strategy_family": "proof_decomposition_variant",
        "expected_artifact_type": "proof_artifact",
        "required_inputs": ["proof_plan_only", "target=claim-1"],
    }
    assert strategy_fingerprint(**inputs) == strategy_fingerprint(**inputs)


@pytest.mark.parametrize(
    ("gap_type", "expected_family"),
    [
        ("needs_retrieval_expansion", "retrieval_query_variant"),
        ("needs_formal_proof", "proof_decomposition_variant"),
        ("needs_python_experiment", "experiment_metric_variant"),
        ("needs_claim_removal", "claim_removal_variant"),
    ],
)
def test_exhausted_gaps_get_safe_diversified_strategies(
    tmp_path,
    gap_type: str,
    expected_family: str,
) -> None:
    run_id = f"run-strategy-{gap_type}"
    _write_exhausted_gap_inputs(tmp_path, run_id=run_id, gap_type=gap_type)

    report = build_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert report.candidate_gap_count == 1
    assert report.selected_strategy_count == 1
    assert any(option.strategy_family == expected_family for option in report.strategy_options)
    assert all(
        "network" not in option.alternative_action.casefold()
        for option in report.strategy_options
    )
    assert report.publication_ready is False


def test_unsafe_strategy_is_not_automation_ready() -> None:
    common = {
        "strategy_id": "strategy-unsafe",
        "gap_fingerprint": "b" * 64,
        "target_claim_id_optional": "claim-1",
        "target_section_optional": "Demonstration Status",
        "gap_type": "needs_python_experiment",
        "original_recommended_action": "Plan an experiment.",
        "strategy_family": "experiment_dataset_variant",
        "expected_artifact_type": "experiment_artifact",
        "novel_relative_to_previous_attempts": True,
        "automation_ready": True,
        "selected": False,
        "rationale": "Test safety classification.",
        "safety_notes": [],
    }
    network = GapStrategyOption(
        **common,
        alternative_action="Call an external API over the network.",
        strategy_fingerprint="c" * 64,
        required_inputs=["external api"],
    )
    arbitrary_python = GapStrategyOption(
        **common,
        alternative_action="Execute arbitrary Python supplied by the spec.",
        strategy_fingerprint="d" * 64,
        required_inputs=["arbitrary python"],
    )
    assert strategy_is_automation_ready(network) is False
    assert strategy_is_automation_ready(arbitrary_python) is False


def test_strategy_diversification_persists_and_detects_duplicates(tmp_path) -> None:
    run_id = "run-strategy-persistence"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    _write_exhausted_gap_inputs(
        tmp_path,
        run_id=run_id,
        gap_type="needs_formal_proof",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    first = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    second = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    third = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    fourth = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspected = inspect_gap_strategy_diversification(run_id=run_id, root=tmp_path)
    cli_json = CliRunner().invoke(
        app,
        [
            "inspect-gap-strategy-diversification",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert first.report.selected_strategy_count == 1
    assert second.report.selected_strategy_count == 1
    assert second.report.duplicate_strategy_count >= 1
    assert third.report.selected_strategy_count == 1
    assert fourth.report.selected_strategy_count == 0
    assert fourth.report.duplicate_strategy_count == fourth.report.strategy_option_count
    assert first.report_artifact.path.endswith("gap-strategy-diversification-0001.json")
    assert first.report_markdown_artifact.path.endswith("gap-strategy-diversification-0001.md")
    assert inspected["strategy_diversification_present"] is True
    assert inspected["duplicate_strategy_count"] >= 1
    assert inspected["publication_ready"] is False
    assert cli_json.exit_code == 0, cli_json.output
    assert json.loads(cli_json.output)["strategy_diversification_present"] is True


def test_autonomous_plan_executor_deduplicates_equivalent_planned_specs(
    tmp_path,
) -> None:
    run_id = "run-autonomous-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=map_result.map_artifact.path,
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-proof",
                target_type="claim",
                target_claim_id_optional="fixture-theorem-claim",
                target_section_optional="Method and Model",
                current_support_status="unsupported",
                gap_type="needs_formal_proof",
                recommended_action="Plan a scoped formal proof attempt.",
                priority="high",
                blocking=True,
                rationale="Fixture theorem requires a passed checker.",
                expected_artifact_type="proof_artifact",
                automation_ready=True,
            )
        ],
        ready_for_formal_proof_attempt=True,
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    first = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    second = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    proof_specs = [
        path
        for path in reports.glob("proof-obligation-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)

    assert first.report.actions_applied == 1
    assert second.report.duplicate_specs_skipped == 1
    assert len(proof_specs) == 1
    assert history["gap_attempt_history_present"] is True
    assert history["gap_attempt_count"] >= 1
    assert dedup["planned_spec_dedup_index_present"] is True
    assert dedup["duplicate_planned_spec_count"] >= 1


def test_autonomous_plan_executor_blocks_corrupt_plan_with_human_intervention(
    tmp_path,
) -> None:
    run_id = "run-autonomous-executor-corrupt"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )

    result = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        execution_mode="dry-run",
        executor_backend="deterministic",
    )

    assert result.report.execution_status == "blocked"
    assert result.report.requires_human_intervention is True
    assert result.report.human_intervention_reason_optional
    assert result.report.publication_ready is False


def test_planned_spec_execution_dry_run_does_not_create_evidence(tmp_path) -> None:
    run_id = "run-planned-spec-dry-run"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(reports, run_id=run_id)
    _write_planned_proof_spec(reports, run_id=run_id)
    _write_retrieval_expansion_request(reports, run_id=run_id)
    claim_map = reports / "claim-evidence-map.json"
    claim_map_hash = sha256_file(claim_map)

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="dry-run",
        spec_executor_backend="deterministic_local",
    )
    inspected = inspect_planned_spec_execution(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "dry_run_completed"
    assert result.report.spec_count == 3
    assert result.report.experiment_artifacts_created == 0
    assert result.report.proof_artifacts_created == 0
    assert result.report.claim_evidence_map_rebuilt is False
    assert sha256_file(claim_map) == claim_map_hash
    assert not list(reports.glob("experiment-artifact-*.json"))
    assert not list(reports.glob("proof-artifact-*.json"))
    assert inspected["planned_spec_execution_count"] == 1
    assert lint["planned_spec_execution_present"] is True
    assert lint["latest_planned_spec_execution_mode"] == "dry_run"
    assert lint["publication_ready"] is False


def test_planned_spec_execution_apply_runs_local_templates_and_rechecks(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-apply"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(
        reports,
        run_id=run_id,
        spec_id="experiment-spec-fixture-001",
        target_claim_id="experiment-claim-1",
        target_section="Demonstration Status",
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-plan-001",
        target_claim_id="fixture-theorem-claim",
    )
    _write_retrieval_expansion_request(reports, run_id=run_id)

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    inspected = inspect_planned_spec_execution(run_id=run_id, root=tmp_path)
    proof = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    experiment = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "completed"
    assert result.report.experiment_specs_executed == 1
    assert result.report.proof_specs_executed == 1
    assert result.report.retrieval_specs_executed == 1
    assert result.report.experiment_artifacts_created == 1
    assert result.report.proof_artifacts_created == 1
    assert result.report.retrieval_artifacts_created == 1
    assert result.report.claim_evidence_map_rebuilt is True
    assert result.report.autonomous_plan_rebuilt is True
    assert result.report.release_rechecked is True
    assert result.report.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert inspected["latest_planned_spec_execution_mode"] == "apply"
    assert experiment["completed_experiment_count"] == 1
    assert proof["informal_proof_artifact_count"] == 1
    assert proof["formal_verification_passed_count"] == 0
    assert lint["planned_spec_execution_present"] is True
    assert lint["experiment_artifacts_created"] == 1
    assert lint["proof_artifacts_created"] == 1
    assert lint["retrieval_artifacts_created"] == 1
    assert lint["publication_ready"] is False


def test_planned_spec_execution_skips_equivalent_duplicate_specs(tmp_path) -> None:
    run_id = "run-planned-spec-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-dedup-a",
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-dedup-b",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    proof_artifacts_after_first = sorted(reports.glob("proof-artifact-*.json"))
    second = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)

    assert result.report.spec_count == 2
    assert result.report.proof_specs_executed == 1
    assert result.report.duplicate_specs_skipped == 1
    assert result.report.items[1].execution_status == "skipped"
    assert second.report.proof_specs_executed == 0
    assert second.report.unique_specs_executed == 0
    assert second.report.duplicate_specs_skipped == 2
    assert second.report.proof_artifacts_created == 0
    assert sorted(reports.glob("proof-artifact-*.json")) == proof_artifacts_after_first
    assert dedup["duplicate_planned_spec_count"] >= 1
    assert history["gap_attempt_history_present"] is True
    assert history["gap_attempt_count"] >= 1


def test_planned_spec_execution_fixture_formal_proof_is_scoped(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-formal-fixture"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-formal-001",
        target_claim_id=(
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-"
            "conjecture-form"
        ),
        suggested_checker="deterministic fixture formal proof checker",
        required_artifact_type="deterministic fixture formal verified passed artifact",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    proof = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.proof_artifacts_created == 1
    assert proof["formal_verification_passed_count"] == 1
    assert lint["proof_artifact_count"] == 1
    assert lint["formal_verification_passed_count"] == 1
    assert lint["publication_ready"] is False


def test_planned_spec_execution_failed_experiment_does_not_create_artifact(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-failed-experiment"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(
        reports,
        run_id=run_id,
        spec_id="experiment-spec-force-failed-001",
        hypothesis_or_question="force_failed_experiment",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    experiment = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "completed_with_deferred_specs"
    assert result.report.specs_rejected == 1
    assert result.report.experiment_artifacts_created == 0
    assert experiment["experiment_artifact_count"] == 0
    assert result.report.publication_ready is False


def test_autonomous_loop_runs_plan_specs_and_updates_bundle_views(tmp_path) -> None:
    run_id = "run-autonomous-loop"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=2,
    )
    inspected = inspect_autonomous_loop(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    reports = tmp_path / "runs" / run_id / "reports"

    assert result.persistence.commit.action_type == ControllerActionType.AUTONOMOUS_LOOP_WRITTEN
    assert result.report.iterations_completed >= 1
    assert result.report.loop_status in {
        "completed",
        "completed_with_deferred_gaps",
        "stopped_no_progress",
        "stopped_max_iterations",
    }
    assert result.report.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert result.report.implies_publication_readiness is False
    assert result.report.is_verification_evidence is False
    assert result.report.requires_human_intervention is False
    assert result.report.iterations[0].claim_evidence_map_path
    assert result.report.iterations[0].autonomous_plan_path
    assert result.report.iterations[0].autonomous_execution_report_path
    assert result.report.iterations[0].planned_spec_execution_report_path
    assert result.report.iterations[0].release_report_path
    assert (reports / "autonomous-loop-0001.json").is_file()
    assert (reports / "autonomous-loop-index-0001.json").is_file()
    assert (reports / "autonomous-loop-iteration-0001-001.json").is_file()
    assert inspected["autonomous_loop_present"] is True
    assert inspected["autonomous_loop_count"] == 1
    assert lint["autonomous_loop_present"] is True
    assert lint["autonomous_loop_count"] == 1
    assert lint["latest_autonomous_loop_iterations_completed"] >= 1
    assert lint["autonomous_loop_requires_human_intervention"] is False
    assert lint["publication_ready"] is False
    assert reviewer["autonomous_loop_present"] is True
    assert reviewer["latest_autonomous_loop_status"] == result.report.loop_status
    assert reviewer["publication_ready"] is False


def test_autonomous_loop_stops_before_max_iterations_for_exhausted_gaps(tmp_path) -> None:
    run_id = "run-autonomous-loop-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=5,
        max_attempts_per_gap=1,
    )
    inspected = inspect_autonomous_loop(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)

    assert result.report.iterations_completed < 5
    assert result.report.loop_status in {
        "completed_with_deferred_gaps",
        "stopped_no_progress",
        "completed",
    }
    assert result.report.stop_reason != "max_iterations_reached"
    assert result.report.publication_ready is False
    assert inspected["gap_exhausted_no_progress_count"] >= 0
    assert lint["gap_attempt_history_present"] is True
    assert lint["planned_spec_dedup_index_present"] is True
    assert lint["latest_autonomous_loop_stop_reason"] != "max_iterations_reached"
    assert history["gap_attempt_history_present"] is True
    assert dedup["planned_spec_dedup_index_present"] is True


def test_autonomous_loop_diversifies_before_final_deferral(tmp_path) -> None:
    run_id = "run-autonomous-loop-strategy-diversification"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=6,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )
    strategy = inspect_gap_strategy_diversification(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.strategy_diversification_enabled is True
    assert result.report.strategy_option_count >= 1
    assert result.report.selected_strategy_count >= 1
    assert result.report.iterations_completed <= 6
    assert result.report.stop_reason != "max_iterations_reached"
    assert strategy["strategy_diversification_present"] is True
    assert strategy["strategy_option_count"] >= 1
    assert history["strategy_attempt_count"] >= 1
    assert any(
        record["current_gap_status"]
        in {
            "exhausted_initial_strategy",
            "exhausted_all_strategies",
            "deferred_after_diversification",
            "resolved",
        }
        for record in history["records"]
    )
    assert lint["strategy_diversification_present"] is True
    assert lint["selected_strategy_count"] >= 0
    assert lint["publication_ready"] is False
    assert reviewer["strategy_diversification_present"] is True
    assert reviewer["publication_ready"] is False


def test_autonomous_loop_blocks_corrupt_claim_evidence_map(tmp_path) -> None:
    run_id = "run-autonomous-loop-corrupt-map"
    _prepare_run(tmp_path, run_id=run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "claim-evidence-map.json").write_text("{not-json}\n", encoding="utf-8")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        loop_backend="deterministic",
        max_iterations=1,
    )

    assert result.report.loop_status == "blocked_requires_human_intervention"
    assert result.report.stop_reason == "safety_gate_blocked"
    assert result.report.iterations_completed == 0
    assert result.report.requires_human_intervention is True
    assert result.report.publication_ready is False


def test_evidence_aware_refresh_writes_bounded_artifact_wording_and_rechecks_gates(
    tmp_path,
) -> None:
    run_id = "run-evidence-aware-refresh"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    report_path = tmp_path / "runs" / run_id / "reports" / "evidence-aware-refresh-report.json"
    refreshed_path = (
        tmp_path / "runs" / run_id / "reports" / "evidence-aware-refreshed-manuscript-draft.md"
    )
    report = EvidenceAwareRefreshReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    refreshed = refreshed_path.read_text(encoding="utf-8")
    lowered = refreshed.casefold()
    assert result.persistence.commit.action_type == (
        ControllerActionType.EVIDENCE_AWARE_REFRESH_WRITTEN
    )
    assert report.refresh_backend == "deterministic"
    assert report.proof_language_inserted is True
    assert report.experiment_language_inserted is True
    assert report.claim_support_rechecked_after_refresh is True
    assert report.claim_evidence_map_rechecked_after_refresh is True
    assert report.citation_safety_rechecked_after_refresh is True
    assert "formal proof artifact linked to a specific mapped claim" in lowered
    assert "completed experiment artifact linked to a bounded result claim" in lowered
    assert "does not establish novelty" in lowered
    assert "does not imply broad empirical validation" in lowered
    assert "publication readiness" in lowered
    assert "publication ready" not in lowered

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["evidence_aware_refresh_report_present"] is True
    assert lint["evidence_aware_refresh_backend"] == "deterministic"
    assert lint["proof_language_inserted"] is True
    assert lint["experiment_language_inserted"] is True
    assert lint["claim_evidence_map_rechecked_after_refresh"] is True
    assert lint["claim_support_rechecked_after_refresh"] is True
    assert lint["citation_safety_rechecked_after_refresh"] is True
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_support_forbidden_claim_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["publication_ready"] is False
    assert result.release_status in {
        "ReadyForHumanReview",
        "ReadyForHumanReviewWithWarnings",
    }

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["proof_supported_claim_count"] >= 1
    assert reviewer["experiment_supported_claim_count"] >= 1
    assert reviewer["citation_supported_claim_count"] >= 0
    assert reviewer["publication_ready"] is False


def test_evidence_aware_refresh_does_not_use_informal_proof_as_formal_wording(
    tmp_path,
) -> None:
    run_id = "run-evidence-aware-refresh-informal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_type="informal_proof_note",
        checker_status="not_checked",
        is_verification_evidence=False,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    assert result.report.proof_language_inserted is False
    assert "formal proof artifact linked" not in result.refreshed_markdown.casefold()
    assert result.report.implies_publication_readiness is False
    assert result.report.creates_scientific_validation is False


def test_evidence_aware_refresh_blocks_unsupported_claim_evidence_map(tmp_path) -> None:
    run_id = "run-evidence-aware-refresh-blocked"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        checker_status="failed",
        is_verification_evidence=False,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    with pytest.raises(EvidenceAwareRefreshError, match="unsupported non-scaffold"):
        refresh_evidence_aware_manuscript(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
        )

    assert not (
        tmp_path / "runs" / run_id / "reports" / "evidence-aware-refreshed-manuscript-draft.md"
    ).exists()


def test_human_review_reconciliation_applies_rejects_and_defers_safely(
    tmp_path,
) -> None:
    run_id = "run-human-review-reconciliation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        review_status="reviewed_with_blocking_changes",
        requested_changes=[
            "Clarify the problem framing and intended research question.",
            "Add an evidence-boundary clarification.",
            "Reference the existing formal proof artifact within its mapped scope.",
            "Mention the existing experiment artifact within its bounded result scope.",
            "Say this manuscript is novel.",
            "Say this manuscript is publication ready.",
            "State that the experiment validates the method broadly.",
            "State the theorem is proven without a matching proof artifact.",
            "Cite a rejected source for the background claim.",
            "Run expanded retrieval before stronger background claims.",
        ],
        blocking_concerns=["Requested changes require deterministic reconciliation."],
    )
    ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    result = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.HUMAN_REVIEW_RECONCILIATION_WRITTEN
    )
    assert result.report.applied_change_count == 4
    assert result.report.rejected_change_count == 4
    assert result.report.deferred_change_count == 2
    assert result.report.requires_new_evidence_count == 2
    assert result.report.claim_support_rechecked_after_reconciliation is True
    assert result.report.claim_evidence_map_rechecked_after_reconciliation is True
    assert result.report.citation_safety_rechecked_after_reconciliation is True
    assert result.report.release_rechecked_after_reconciliation is True
    outcomes = {item.outcome for item in result.report.change_outcomes}
    assert "applied_safe_text_revision" in outcomes
    assert "applied_boundary_clarification" in outcomes
    assert "applied_existing_evidence_reference" in outcomes
    assert "rejected_forbidden_authority_claim" in outcomes
    assert "rejected_unsupported_claim" in outcomes
    assert "deferred_requires_proof_artifact" in outcomes
    assert "deferred_requires_retrieval_expansion" in outcomes
    assert "say this manuscript is novel" not in result.reconciled_markdown.casefold()
    assert "say this manuscript is publication ready" not in (result.reconciled_markdown.casefold())
    assert "formal proof artifact linked to a specific mapped claim" in (
        result.reconciled_markdown.casefold()
    )
    assert "completed experiment artifact linked to a bounded result claim" in (
        result.reconciled_markdown.casefold()
    )
    assert result.claim_evidence_map.unsupported_non_scaffold_claim_ids == []

    report_path = (
        tmp_path / "runs" / run_id / "reports" / "human-review-reconciliation-cycle-001.json"
    )
    markdown_report_path = report_path.with_suffix(".md")
    manuscript_path = tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-001.md"
    assert report_path.is_file()
    assert markdown_report_path.is_file()
    assert manuscript_path.is_file()
    report = HumanReviewReconciliationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert report.creates_scientific_validation is False
    assert report.implies_publication_readiness is False
    assert report.is_verification_evidence is False
    inspected = inspect_human_review_reconciliation(run_id=run_id, root=tmp_path)
    assert inspected["human_review_reconciliation_present"] is True
    assert inspected["publication_ready"] is False

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_reconciliation_present"] is True
    assert lint["human_review_applied_change_count"] == 4
    assert lint["human_review_rejected_change_count"] == 4
    assert lint["human_review_deferred_change_count"] == 2
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["citation_registry_sources_all_accepted"] is True
    assert lint["publication_ready"] is False

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["summary_path"].endswith(
        "reviewer-bundle-summary-after-reconciliation-cycle-001.json"
    )
    assert reviewer["human_review_reconciliation_present"] is True
    assert reviewer["human_review_applied_change_count"] == 4
    assert reviewer["human_review_remaining_requested_changes"]
    assert reviewer["publication_ready"] is False


def test_structured_reviewer_requests_support_two_immutable_cycles(tmp_path) -> None:
    run_id = "run-structured-reviewer-cycles"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)
    review = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    ).review
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)
    proof_link = next(
        link for link in claim_map.links if link.support_type == "formal_proof_verification"
    )
    experiment_link = next(
        link for link in claim_map.links if link.support_type == "experiment_result"
    )
    request_file_1 = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="request-set-001",
        review_id=review.review_id,
        target_artifact_path=(
            f"runs/{run_id}/reports/evidence-aware-refreshed-manuscript-draft.md"
        ),
        requests=[
            {
                "request_id": "clarify",
                "target_type": "section",
                "target_section_optional": "Introduction and Problem Framing",
                "requested_action": "clarify_wording",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "proof",
                "target_type": "proof_artifact",
                "target_section_optional": "Claim and Evidence Boundaries",
                "target_claim_id_optional": proof_link.claim_id,
                "target_evidence_artifact_id_optional": (
                    proof_link.supporting_proof_artifact_ids[0]
                ),
                "requested_action": "add_existing_proof_reference",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "experiment",
                "target_type": "experiment_artifact",
                "target_section_optional": "Demonstration Status",
                "target_claim_id_optional": experiment_link.claim_id,
                "target_evidence_artifact_id_optional": (
                    experiment_link.supporting_experiment_artifact_ids[0]
                ),
                "requested_action": "add_existing_experiment_reference",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "forbidden",
                "target_type": "release_report",
                "requested_action": "forbidden_publication_ready_request",
                "priority": "blocking",
                "requires_new_evidence": False,
            },
            {
                "request_id": "new-proof",
                "target_type": "claim",
                "target_claim_id_optional": proof_link.claim_id,
                "requested_action": "request_new_proof_artifact",
                "priority": "medium",
                "requires_new_evidence": True,
            },
        ],
    )
    intake_1 = ingest_reviewer_change_requests(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        request_file=request_file_1,
    )
    assert intake_1.request_set_number == 1
    cycle_1 = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert cycle_1.report.cycle_number == 1
    assert cycle_1.report.applied_change_count == 3
    assert cycle_1.report.rejected_change_count == 1
    assert cycle_1.report.deferred_change_count == 1
    cycle_1_path = tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-001.md"
    cycle_1_hash = sha256_file(cycle_1_path)

    request_file_2 = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="request-set-002",
        review_id=review.review_id,
        target_artifact_path=(f"runs/{run_id}/reports/reconciled-manuscript-cycle-001.md"),
        requests=[
            {
                "request_id": "boundary-cycle-2",
                "target_type": "section",
                "target_section_optional": "Limitations",
                "requested_action": "add_boundary_language",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    ingest_reviewer_change_requests(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        request_file=request_file_2,
    )
    cycle_2 = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert cycle_2.report.cycle_number == 2
    assert cycle_2.report.applied_change_count == 1
    assert sha256_file(cycle_1_path) == cycle_1_hash
    assert (tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-002.md").is_file()
    index = HumanReviewReconciliationIndex.model_validate_json(
        (tmp_path / cycle_2.reconciliation_index_artifact.path).read_text()
    )
    assert index.latest_cycle == 2
    assert index.cycle_count == 2
    assert index.current_preferred_reconciled_manuscript.endswith(
        "reconciled-manuscript-cycle-002.md"
    )
    inspected = inspect_reviewer_change_requests(run_id=run_id, root=tmp_path)
    assert inspected["reviewer_request_set_count"] == 2
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_reconciliation_cycle_count"] == 2
    assert lint["latest_reconciliation_cycle"] == 2
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["publication_ready"] is False


def test_structured_reviewer_request_intake_rejects_invalid_targets(tmp_path) -> None:
    run_id = "run-structured-reviewer-invalid"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=_write_human_review_fixture(tmp_path, run_id=run_id),
    ).review
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    target = f"runs/{run_id}/reports/revised-manuscript-draft.md"

    unknown_section = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="unknown-section",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "unknown-section-request",
                "target_type": "section",
                "target_section_optional": "Unknown Section",
                "requested_action": "clarify_wording",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="unknown target section"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=unknown_section,
        )

    unknown_claim = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="unknown-claim",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "unknown-claim-request",
                "target_type": "claim",
                "target_claim_id_optional": "missing-claim-id",
                "requested_action": "request_new_proof_artifact",
                "priority": "medium",
                "requires_new_evidence": True,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="unknown claim"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=unknown_claim,
        )

    rejected_citation = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="rejected-citation",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "rejected-citation-request",
                "target_type": "citation",
                "requested_action": "add_existing_citation",
                "requested_text_optional": "RejectedSourceKey",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="accepted registry"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=rejected_citation,
        )


def test_claim_evidence_map_links_human_review_occurrence_only(tmp_path) -> None:
    run_id = "run-claim-evidence-human-review"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="human-review-claim-1",
                claim_class="pipeline_status_claim",
                sentence_snippet=("Human review recorded readiness for evidence generation."),
                support_status="not_required_scaffold",
            ),
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                sentence_snippet="Human review confirms this theorem.",
                support_status="forbidden_claim_without_evidence",
            ),
        ],
    )
    _write_human_review_artifact_report(tmp_path, run_id=run_id)

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    by_id = {link.claim_id: link for link in claim_map.links}
    assert by_id["human-review-claim-1"].support_type == "human_review_occurrence"
    assert by_id["proof-claim-1"].support_status == "unsupported"
    assert claim_map.summary_counts["human_reviewed_claim"] == 1


def test_safe_repair_separates_pre_and_post_repair_warnings(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-safe-repair-warnings")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-safe-repair-warnings/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-safe-repair-warnings",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=_UnsafeFirstProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-safe-repair-warnings",
            write_report=True,
        ),
        enable_safe_repair=True,
    )

    assert result.revision_result is not None
    repair_ref = result.revision_result.safe_repair_report_artifact
    assert repair_ref is not None
    payload = json.loads((tmp_path / repair_ref.path).read_text(encoding="utf-8"))
    assert payload["pre_repair_warnings"]
    assert payload["repaired_warnings"]
    for repaired_warning in payload["repaired_warnings"]:
        assert repaired_warning in payload["pre_repair_warnings"]
        assert repaired_warning not in payload["post_repair_warnings"]
        assert repaired_warning not in result.report.warnings
    assert all(
        "synthetic or MVP evidence is described as real-world empirical validation" not in warning
        for warning in result.report.warnings
    )


def test_generate_paper_render_check_fails_closed_without_external_tools(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-render")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-render",
            "--render-check",
        ],
    )

    assert result.exit_code == 1
    assert "External render tools are disabled" in result.stderr


def test_generate_paper_missing_manuscript_plan_fails_clearly(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-plan",
        ],
    )

    assert result.exit_code == 1
    assert "No manuscript plan found" in result.stderr


def test_repeated_generate_paper_write_report_fails_by_default(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-repeat")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 1
    assert "already exists" in second.stderr


def test_generate_paper_skip_if_complete_reuses_existing_report(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-skip")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
            "--rerun-policy",
            "skip-if-complete",
            "--json",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["artifacts"]["full_paper_generation_report"] is not None


def test_full_paper_generation_does_not_mutate_claim_or_evidence_tables(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-boundary")
    before = _claim_table_snapshot(tmp_path, "run-boundary")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-boundary",
            "--apply-safe-fake-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _claim_table_snapshot(tmp_path, "run-boundary") == before
    ledger = ResearchLedger(tmp_path / "runs" / "run-boundary" / "ledger.sqlite")
    actions = [commit.action_type for commit in ledger.list_commits("run-boundary")]
    assert ControllerActionType.FULL_PAPER_GENERATION_WRITTEN in actions


def test_quality_aware_generation_improves_lint_on_safe_fixture(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-quality-aware")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-quality-aware/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-quality-aware",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=_QualityProseGenerator(),
        config=FullPaperGenerationConfig(run_id="run-quality-aware", write_report=True),
    )

    markdown_path = tmp_path / "runs/run-quality-aware/reports/complete-manuscript-draft.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    lint = lint_paper_bundle_summary(run_id="run-quality-aware", root=tmp_path)

    assert result.report.publication_ready is False
    assert lint["paper_release_status"] is None
    assert lint["release_status_unchanged"] is True
    assert lint["quality_status"] in {"DraftQualityPass", "DraftQualityWarnings"}
    assert 7 <= lint["section_count"] <= 10
    assert lint["title_is_placeholder"] is False
    assert lint["semantic_checks"]["problem_statement_present"] is True
    assert lint["semantic_checks"]["central_contribution_present"] is True
    assert lint["semantic_checks"]["method_summary_present"] is True
    assert lint["semantic_checks"]["evidence_boundary_statement_present"] is True
    assert lint["semantic_checks"]["provenance_present"] is True
    assert markdown.lower().count("central contribution") == 1
    assert not any(
        "main result is not stated" in finding.message
        for finding in (result.critic_result.critic_report.findings if result.critic_result else [])
    )
    assert "## Empirical Results and Discussion" not in markdown
    assert "## Bibliography" not in markdown
    assert "## Demonstration Status" in markdown


def _write_claim_map_reports(
    tmp_path,
    *,
    run_id: str,
    items: list[ClaimSupportItem],
    citation_registry: CitationRegistry | None = None,
    retrieval_quality: RetrievalQualityReport | None = None,
) -> Path:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    audit = ClaimSupportAuditReport(
        run_id=run_id,
        citation_registry_present=citation_registry is not None,
        citation_policy="registry-only" if citation_registry is not None else "none",
        claim_support_items=items,
        summary_counts={"total_sentences": len(items)},
        unsupported_items=[
            item
            for item in items
            if item.support_status
            in {
                "missing_required_citation",
                "forbidden_claim_without_evidence",
                "unsupported_external_claim",
            }
        ],
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    (reports / "claim-support-audit.json").write_text(
        audit.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    if citation_registry is not None:
        (reports / "citation-registry.json").write_text(
            citation_registry.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    if retrieval_quality is not None:
        (reports / "retrieval-quality-report.json").write_text(
            retrieval_quality.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    return reports


def _write_claim_evidence_map_report(
    tmp_path,
    claim_map: ClaimEvidenceMap,
) -> None:
    reports = tmp_path / "runs" / claim_map.run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "claim-evidence-map.json").write_text(
        claim_map.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _claim_support_item(
    *,
    sentence_id: str,
    claim_class: str,
    sentence_hash: str | None = None,
    sentence_snippet: str = "Fixture claim sentence.",
    citation_keys_present: list[str] | None = None,
    supporting_source_ids: list[str] | None = None,
    support_status: str = "not_required_scaffold",
) -> ClaimSupportItem:
    return ClaimSupportItem(
        sentence_id=sentence_id,
        section_name="Fixture Section",
        sentence_text_hash=sentence_hash or "a" * 64,
        sentence_snippet=sentence_snippet,
        claim_class=claim_class,
        citation_keys_present=citation_keys_present or [],
        requires_citation=claim_class
        in {
            "literature_background_claim",
            "source_context_claim",
            "external_factual_claim",
        },
        requires_citation_reason=(
            "positive_literature_claim"
            if claim_class == "literature_background_claim"
            else "positive_source_context_claim"
            if claim_class == "source_context_claim"
            else "positive_external_claim"
            if claim_class == "external_factual_claim"
            else "claim_class_no_citation_required"
        ),
        required_support_type="accepted_registry_source" if citation_keys_present else "none",
        supporting_source_ids=supporting_source_ids or [],
        support_status=support_status,
        unsupported_reason=None
        if support_status in {"registry_supported", "not_required_scaffold"}
        else "fixture unsupported",
        paragraph_index=0,
        sentence_index=0,
        citation_use="background_context" if citation_keys_present else "none",
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _citation_registry_fixture(
    *,
    run_id: str,
    source_status: str = "retrieved",
    accepted_for_registry: bool = True,
) -> CitationRegistry:
    record = CitationRecord(
        citation_id="citation-source-1",
        citation_key="smith2021",
        source_id="source-1",
        title="Fixture Human Geography Source",
        authors=["Smith"],
        year=2021,
        venue="Fixture Journal",
        provider="fixture",
        retrieval_backend="local",
        retrieved_at="2026-06-30T00:00:00Z",
        raw_metadata_hash="b" * 64,
        source_status=source_status,
        source_summary="A bounded fixture source for background context.",
        accepted_for_registry=accepted_for_registry,
        may_support_background_context=True,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    return CitationRegistry(
        run_id=run_id,
        citations=[record],
        bibliography=[],
        citation_key_policy="deterministic_fixture",
        citation_policy="registry-only",
        retrieval_backend="local",
        source_registry_hash="c" * 64,
        source_count=1,
        accepted_source_count=1 if accepted_for_registry else 0,
        rejected_source_count=0 if accepted_for_registry else 1,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _retrieval_quality_fixture(
    *,
    run_id: str,
    accepted_source_ids: list[str] | None = None,
    rejected_source_ids: list[str] | None = None,
    rejection_reasons: dict[str, str] | None = None,
) -> RetrievalQualityReport:
    accepted = ["source-1"] if accepted_source_ids is None else accepted_source_ids
    rejected = rejected_source_ids or []
    return RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=len(accepted) + len(rejected),
        accepted_source_count=len(accepted),
        rejected_source_count=len(rejected),
        queries_used=["fixture query"],
        coverage_limitations=["fixture retrieval is bounded"],
        adequacy_status="bounded_context_only",
        accepted_source_ids=accepted,
        rejected_source_ids=rejected,
        rejection_reasons=rejection_reasons or {},
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _write_proof_artifact_report(tmp_path, *, run_id: str, **kwargs) -> None:
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id, **kwargs)
    proof = ProofArtifact.model_validate_json(proof_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"proof-artifact-{proof.proof_id}.json").write_text(
        proof.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_experiment_artifact_report(tmp_path, *, run_id: str, **kwargs) -> None:
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        **kwargs,
    )
    experiment = ExperimentArtifact.model_validate_json(experiment_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"experiment-artifact-{experiment.experiment_id}.json").write_text(
        experiment.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_human_review_artifact_report(tmp_path, *, run_id: str) -> None:
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)
    review = HumanReviewArtifact.model_validate_json(review_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "human-review-artifact.json").write_text(
        review.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_run(tmp_path, *, run_id: str) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )


def _prepare_reviewable_bundle(tmp_path, *, run_id: str) -> None:
    _prepare_run(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id=run_id,
            write_report=True,
            quality_repair_backend="deterministic",
        ),
        enable_safe_repair=True,
    )
    run_full_paper_release_gate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
    )


def _write_human_review_fixture(
    tmp_path,
    *,
    run_id: str,
    reviewed_run_id: str | None = None,
    review_status: str = "reviewed_ready_for_evidence_generation",
    checklist_items: list[str] | None = None,
    blocking_concerns: list[str] | None = None,
    non_blocking_comments: list[str] | None = None,
    requested_changes: list[str] | None = None,
    recommended_next_action: str = (
        "Proceed to evidence generation planning without publication-readiness claims."
    ),
    reviewer_attestation: str = (
        "I performed this human review locally and understand that it records review "
        "occurrence only."
    ),
    filename: str = "human-review.json",
) -> Path:
    artifact_run_id = reviewed_run_id or run_id
    payload = {
        "run_id": run_id,
        "review_id": f"review-{run_id}",
        "reviewer_name_optional": "Fixture Reviewer",
        "reviewer_role": "internal_human_reviewer",
        "reviewer_is_human": True,
        "llm_generated": False,
        "reviewed_artifact_paths": [
            f"runs/{artifact_run_id}/reports/revised-manuscript-draft.md",
            f"runs/{artifact_run_id}/reports/reviewer-bundle-summary.json",
            f"runs/{artifact_run_id}/reports/claim-support-audit.json",
        ],
        "reviewed_at": "2026-06-30T00:00:00Z",
        "review_status": review_status,
        "checklist_items": (
            [
                "problem framing checked",
                "citation registry checked",
                "accepted sources checked",
                "claim-support audit checked",
                "evidence gaps acknowledged",
                "proof artifact absent acknowledged",
                "experiment artifact absent acknowledged",
                "publication_ready remains false acknowledged",
            ]
            if checklist_items is None
            else checklist_items
        ),
        "blocking_concerns": blocking_concerns or [],
        "non_blocking_comments": non_blocking_comments
        or [
            "The draft can proceed to evidence-generation planning with retrieval limits preserved."
        ],
        "requested_changes": requested_changes or [],
        "accepted_limitations": [
            "Retrieval remains bounded background context only.",
            "Proof artifact is absent.",
            "Experiment artifact is absent.",
            "publication_ready remains false.",
        ],
        "recommended_next_action": recommended_next_action,
        "reviewer_attestation": reviewer_attestation,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_structured_request_set(
    tmp_path,
    *,
    run_id: str,
    request_set_id: str,
    review_id: str,
    target_artifact_path: str,
    requests: list[dict[str, object]],
) -> Path:
    normalized_requests = [
        {
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
            **request,
        }
        for request in requests
    ]
    payload = {
        "run_id": run_id,
        "request_set_id": request_set_id,
        "review_id": review_id,
        "reviewer_name_optional": "Fixture Reviewer",
        "created_at": "2026-07-01T00:00:00Z",
        "target_artifact_path": target_artifact_path,
        "requests": normalized_requests,
        "reviewer_attestation": (
            "I authored these structured requests as a human reviewer and understand "
            "that they do not create evidence or publication readiness."
        ),
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / f"{request_set_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _write_planned_experiment_spec(
    reports: Path,
    *,
    run_id: str,
    spec_id: str = "experiment-spec-fixture-001",
    target_claim_id: str = "experiment-claim-1",
    target_section: str = "Demonstration Status",
    hypothesis_or_question: str = "Can the local synthetic template record bounded metrics?",
) -> Path:
    spec = PlannedExperimentSpec(
        run_id=run_id,
        spec_id=spec_id,
        target_claim_id=target_claim_id,
        target_section=target_section,
        hypothesis_or_question=hypothesis_or_question,
        suggested_dataset="deterministic synthetic calibration fixture",
        suggested_metrics=["bounded_improvement", "method_error"],
        suggested_baselines=["deterministic baseline"],
        suggested_seed_policy="fixed seed 1729",
        expected_output_artifacts=["metrics", "log"],
    )
    path = reports / f"{spec_id}.json"
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_planned_proof_spec(
    reports: Path,
    *,
    run_id: str,
    spec_id: str = "proof-obligation-spec-fixture-001",
    target_claim_id: str = "fixture-theorem-claim",
    statement: str = "A bounded fixture statement requires proof evidence.",
    suggested_checker: str = "explicitly configured local formal proof backend",
    required_artifact_type: str = "passed scoped proof artifact",
) -> Path:
    spec = ProofObligationSpec(
        run_id=run_id,
        spec_id=spec_id,
        target_claim_id=target_claim_id,
        statement=statement,
        suggested_checker=suggested_checker,
        required_artifact_type=required_artifact_type,
    )
    path = reports / f"{spec_id}.json"
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_retrieval_expansion_request(
    reports: Path,
    *,
    run_id: str,
    request_id: str = "retrieval-expansion-request-fixture-001",
) -> Path:
    request = RetrievalExpansionRequest(
        run_id=run_id,
        request_id=request_id,
        target_claim_id_optional=None,
        target_section_optional="Introduction and Problem Framing",
        query_terms=["human", "geography", "bounded", "retrieval"],
        reason="Fixture bounded retrieval expansion request.",
        minimum_source_quality="accepted registry source after deterministic checks",
    )
    path = reports / f"{request_id}.json"
    path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_exhausted_gap_inputs(
    tmp_path: Path,
    *,
    run_id: str,
    gap_type: str,
) -> None:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    gap_fingerprint = "a" * 64
    target_claim = None if gap_type == "needs_retrieval_expansion" else "claim-1"
    target_section = (
        "Demonstration Status"
        if gap_type == "needs_python_experiment"
        else "Method and Model"
        if target_claim
        else None
    )
    expected_artifact = {
        "needs_retrieval_expansion": "retrieval_quality_report",
        "needs_formal_proof": "proof_artifact",
        "needs_python_experiment": "experiment_artifact",
        "needs_claim_removal": "revised_manuscript",
        "needs_claim_downgrade": "revised_manuscript",
    }[gap_type]
    record = GapAttemptRecord(
        gap_fingerprint=gap_fingerprint,
        target_claim_id_optional=target_claim,
        target_section_optional=target_section,
        gap_type=gap_type,
        recommended_action=f"Initial exhausted action for {gap_type}.",
        expected_artifact_type=expected_artifact,
        attempt_count=1,
        no_op_attempt_count=1,
        latest_attempt_status="skipped",
        current_gap_status="exhausted_no_progress",
    )
    history = GapAttemptHistory(
        run_id=run_id,
        history_version=9999,
        gap_count=1,
        attempt_count=1,
        exhausted_gap_count=1,
        records=[record],
        created_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
    )
    plan_item = AutonomousEvidenceGapPlanItem(
        item_id="plan-item-exhausted",
        target_type="claim" if target_claim else "retrieval",
        target_claim_id_optional=target_claim,
        target_section_optional=target_section,
        current_support_status="unsupported",
        gap_type=gap_type,
        recommended_action=record.recommended_action,
        priority="high",
        blocking=False,
        rationale="The initial deterministic strategy made no progress.",
        required_inputs=[f"target={target_claim or 'bounded-context'}"],
        expected_artifact_type=expected_artifact,
        automation_ready=False,
        gap_fingerprint=gap_fingerprint,
        gap_attempt_history_present=True,
        gap_attempt_count=1,
        gap_already_attempted=True,
        gap_exhausted=True,
        automation_ready_after_history=False,
    )
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=f"runs/{run_id}/reports/claim-evidence-map.json",
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[plan_item],
        gap_attempt_history_present=True,
        gap_attempt_count=1,
        exhausted_gap_count=1,
    )
    (reports / "gap-attempt-history-9999.json").write_text(
        history.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_proof_artifact_fixture(
    tmp_path,
    *,
    run_id: str,
    artifact_run_id: str | None = None,
    proof_id: str = "lean-proof-passed-001",
    proof_type: str = "lean_verified",
    claim_ids_or_statement_ids: list[str] | None = None,
    checker_status: str = "passed",
    statement: str = ("A local checker report is linked for a bounded statement in this fixture."),
    is_verification_evidence: bool = True,
    proof_hash: str = "1" * 64,
) -> Path:
    reviewed_run_id = artifact_run_id or run_id
    payload = {
        "run_id": run_id,
        "proof_id": proof_id,
        "proof_type": proof_type,
        "claim_ids_or_statement_ids": claim_ids_or_statement_ids or ["statement-1"],
        "statement": statement,
        "artifact_path_optional": (f"runs/{reviewed_run_id}/reports/revised-manuscript-draft.md"),
        "checker_name_optional": "fixture-local-checker",
        "checker_version_optional": "0.1.0",
        "checker_status": checker_status,
        "checker_log_hash_optional": "2" * 64,
        "proof_hash": proof_hash,
        "review_status": "artifact_scope_not_human_validated",
        "limitations": [
            "This fixture is local proof-artifact intake only.",
            "It does not imply novelty, broad correctness, or publication readiness.",
        ],
        "created_at": "2026-06-30T00:00:00Z",
        "ingested_at": "2026-06-30T00:00:00Z",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": is_verification_evidence,
    }
    path = tmp_path / f"{proof_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_experiment_artifact_fixture(
    tmp_path,
    *,
    run_id: str,
    artifact_run_id: str | None = None,
    experiment_id: str = "completed-experiment-001",
    claim_ids_or_section_ids: list[str] | None = None,
    status: str = "completed",
    result_summary: str = (
        "The local fixture run completed and reports bounded metrics for this run only."
    ),
    config_hash: str = "7" * 64,
) -> Path:
    reviewed_run_id = artifact_run_id or run_id
    payload = {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "experiment_type": "local_synthetic_fixture",
        "claim_ids_or_section_ids": claim_ids_or_section_ids or ["demonstration-status"],
        "hypothesis_or_question": (
            "Can the local fixture record bounded experiment-output intake?"
        ),
        "status": status,
        "dataset_name_optional": "fixture-synthetic-dataset",
        "dataset_hash_optional": "6" * 64,
        "config_hash": config_hash,
        "code_commit_hash_optional": "abc123fixture",
        "command_optional": "factori fixture-experiment --local",
        "metrics": {
            "fixture_metric": 1.0,
            "sample_count": 3,
        },
        "result_summary": result_summary,
        "artifact_paths": [f"runs/{reviewed_run_id}/reports/revised-manuscript-draft.md"],
        "limitations": [
            "This experiment artifact is local to the fixture run.",
            "It does not imply broad empirical validation or publication readiness.",
        ],
        "created_at": "2026-06-30T00:00:00Z",
        "ingested_at": "2026-06-30T00:00:00Z",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / f"{experiment_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _assert_artifact_boundary_flags(
    tmp_path,
    ref: ArtifactRef,
    *,
    is_verification_evidence: bool = False,
) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.content_hash == sha256_file(path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is is_verification_evidence
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


def _assert_non_evidence_artifact(tmp_path, ref: ArtifactRef) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.content_hash == sha256_file(path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


class _UnsafeFirstProseGenerator:
    backend_name = "fake"
    is_fake = True
    external_calls_enabled = False

    def __init__(self) -> None:
        self._delegate = FakeProseGenerator()
        self._calls = 0

    def generate_section(self, section_contract, claim_table) -> GeneratedSectionDraft:
        self._calls += 1
        draft = self._delegate.generate_section(section_contract, claim_table)
        if self._calls != 1:
            return draft
        return draft.model_copy(
            update={
                "content": (
                    "Conjecture. The synthetic result is empirically validated. "
                    "This unsupported sentence is intentionally unsafe."
                ),
                "unsupported_sentences": ["This unsupported sentence is intentionally unsafe."],
            }
        )


class _QualityProseGenerator:
    backend_name = "fake"
    is_fake = True
    external_calls_enabled = False

    def generate_section(self, section_contract, claim_table) -> GeneratedSectionDraft:
        del claim_table
        title = section_contract.section_title
        allowed_claim_ids = list(section_contract.allowed_claim_ids)
        evidence_ids = list(section_contract.allowed_evidence_artifact_ids)
        content = _quality_section_text(title)
        return GeneratedSectionDraft(
            section_id=section_contract.section_id,
            title=title,
            content=content,
            claim_ids=allowed_claim_ids,
            used_claim_ids=allowed_claim_ids,
            used_evidence_artifact_ids=evidence_ids,
            used_citation_ids=[],
            used_citation_keys=[],
            unsupported_sentences=[],
            warnings=[],
        )


def _quality_section_text(title: str) -> str:
    lower = title.lower()
    if "introduction" in lower:
        seed = (
            "The problem framing is explicit: this manuscript studies the selected "
            "branch as a bounded internal research object, not as a verified result. "
            "No retrieval-backed citations are available, so the introduction does "
            "not invent citation markers or bibliography entries."
        )
    elif "method" in lower:
        seed = (
            "The method and model summary describes the deterministic scaffold, the "
            "claim table, and the evidence links as audit objects. The approach keeps "
            "presentation artifacts separate from verification evidence."
        )
    elif "claim" in lower:
        seed = (
            "The claim and evidence boundary section lists only admitted claim IDs "
            "and preserves their labels. It does not transform conjectural, fake, or "
            "presentation material into proof or experiment evidence."
        )
    elif "demonstration" in lower:
        seed = (
            "The demonstration status is a non-evidence MVP account. No real proof, "
            "real experiment, real-world empirical validation, or publication-ready "
            "claim is available from this generated paper package."
        )
    elif "limitation" in lower:
        seed = (
            "The limitations section states that fake validators, LLM prose, citation "
            "absence, and LaTeX export are context only. The draft remains suitable "
            "only for internal human review."
        )
    elif "conclusion" in lower:
        seed = (
            "The conclusion summarizes the bounded contribution and repeats that the "
            "generated manuscript cannot create evidence, upgrade labels, invent "
            "citations, or imply publication readiness."
        )
    else:
        seed = (
            "The abstract states the central message and keeps the scientific status "
            "bounded by the claim table, evidence map, and release warnings."
        )
    return " ".join(seed for _ in range(10))


def _claim_table_snapshot(tmp_path, run_id: str) -> bytes:
    path = tmp_path / "runs" / run_id / "reports" / "claim-table.json"
    return path.read_bytes()
