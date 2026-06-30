from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.claim_adjudication import FakeClaimAdjudicator
from factori.cli import app
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_reviewer_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.hashing import sha256_file
from factori.human_review import (
    HumanReviewIntakeError,
    ingest_human_review,
    inspect_human_review,
)
from factori.ledger import ResearchLedger
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ClaimSupportAuditReport,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationStatus,
    FullPaperReleaseGateConfig,
    GeneratedSectionDraft,
    HumanReviewArtifact,
    PipelineRunConfig,
    PipelineStage,
    QualityRepairReport,
    ReviewerBundleSummary,
)


def test_full_paper_generation_models_are_importable() -> None:
    assert FullPaperGenerationConfig
    assert FullPaperArtifactBundle
    assert FullPaperGenerationReport
    assert QualityRepairReport
    assert ReviewerBundleSummary
    assert HumanReviewArtifact


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
    assert (
        tmp_path / "runs" / "run-report" / "reports" / "complete-manuscript-draft.md"
    ).is_file()
    assert (tmp_path / "runs" / "run-report" / "latex" / "paper.tex").is_file()
    assert (
        tmp_path / "runs" / "run-report" / "reports" / "paper-critic-report.json"
    ).is_file()
    claim_support_path = (
        tmp_path / "runs" / "run-report" / "reports" / "claim-support-audit.json"
    )
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
    report = QualityRepairReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
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
    assert result.artifact_bundle.quality_repair_report_artifact_id == (
        "quality-repair-report"
    )
    assert result.artifact_bundle.revised_manuscript_draft_artifact_id == (
        "revised-manuscript-draft"
    )
    revised = (
        tmp_path
        / "runs/run-quality-repair/reports/revised-manuscript-draft.md"
    ).read_text(encoding="utf-8")
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

    json_path = (
        tmp_path
        / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.json"
    )
    markdown_path = (
        tmp_path / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.md"
    )
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert release.reviewer_summary_artifact is not None
    assert release.reviewer_summary_markdown_artifact is not None
    _assert_non_evidence_artifact(tmp_path, release.reviewer_summary_artifact)
    _assert_non_evidence_artifact(
        tmp_path,
        release.reviewer_summary_markdown_artifact,
    )

    summary = ReviewerBundleSummary.model_validate_json(
        json_path.read_text(encoding="utf-8")
    )
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
    assert summary["summary_path"].endswith(
        "reviewer-bundle-summary-after-human-review.json"
    )
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
        "blocking human-review concerns" in action
        for action in summary["recommended_next_actions"]
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
        "synthetic or MVP evidence is described as real-world empirical validation"
        not in warning
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
                "unsupported_sentences": [
                    "This unsupported sentence is intentionally unsafe."
                ],
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
