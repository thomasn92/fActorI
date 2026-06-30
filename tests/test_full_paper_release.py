from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import generate_full_paper
from factori.full_paper_release import (
    evaluate_full_paper_release,
    run_full_paper_release_gate,
)
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.paper_critic import build_paper_revision_plan, critique_generated_paper
from factori.paper_revision import apply_safe_fake_revision
from factori.persistence import (
    ArtifactWriteSpec,
    persist_artifacts_with_commit,
    persist_markdown_artifact,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimSupportAuditReport,
    ControllerActionType,
    FullPaperGenerationConfig,
    FullPaperReleaseGateConfig,
    FullPaperReleaseReport,
    FullPaperReleaseStatus,
    PipelineRunConfig,
    PipelineStage,
)


def test_full_paper_release_models_are_importable() -> None:
    assert FullPaperReleaseGateConfig
    assert FullPaperReleaseReport
    assert FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW.value == "ReadyForHumanReview"


def test_release_gate_passes_safe_revised_generated_bundle(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "safe")

    report = evaluate_full_paper_release(
        run_id="safe",
        root=tmp_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id="safe"),
    )

    assert report.decision.status in {
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
    }
    assert report.decision.ready_for_human_review is True
    assert report.publication_ready is False
    assert report.evidence_boundary.safe is True
    assert report.revision_status is not None


def test_missing_markdown_draft_blocks(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "missing-draft")
    (tmp_path / "runs/missing-draft/reports/revised-manuscript-draft.md").unlink()

    report = _evaluate(tmp_path, ledger, "missing-draft")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_MISSING_ARTIFACTS
    assert "revised-manuscript-draft" in report.completeness.missing_artifact_ids


def test_missing_latex_source_map_blocks(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "missing-map")
    (tmp_path / "runs/missing-map/latex/revised-latex-source-map.json").unlink()

    report = _evaluate(tmp_path, ledger, "missing-map")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_MISSING_ARTIFACTS
    assert "revised-latex-source-map" in report.completeness.missing_artifact_ids


def test_missing_latex_export_blocks_when_required(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "missing-latex")
    (tmp_path / "runs/missing-latex/latex/revised-paper.tex").unlink()

    report = _evaluate(tmp_path, ledger, "missing-latex")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_MISSING_ARTIFACTS
    assert "revised-paper" in report.completeness.missing_artifact_ids


def test_missing_citation_registry_blocks_when_citations_are_required(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "missing-citations")
    (tmp_path / "runs/missing-citations/reports/citation-registry.json").unlink()

    report = _evaluate(tmp_path, ledger, "missing-citations")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_MISSING_ARTIFACTS
    assert "citation-registry" in report.completeness.missing_artifact_ids


def test_unknown_citation_key_blocks(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "bad-citation")
    _replace_revised_draft(
        tmp_path,
        ledger,
        "bad-citation",
        _revised_text(tmp_path, "bad-citation") + "\nUnknown source [@invented2026].\n",
    )

    report = _evaluate(tmp_path, ledger, "bad-citation")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_CITATION_SAFETY_VIOLATION


def test_retrieval_as_novelty_proof_blocks(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "novelty")
    _replace_revised_draft(
        tmp_path,
        ledger,
        "novelty",
        _revised_text(tmp_path, "novelty") + "\nRetrieval proves novelty.\n",
    )

    report = _evaluate(tmp_path, ledger, "novelty")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_CITATION_SAFETY_VIOLATION


def test_synthetic_as_real_empirical_validation_blocks(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "empirical")
    _replace_revised_draft(
        tmp_path,
        ledger,
        "empirical",
        _revised_text(tmp_path, "empirical") + "\nThe result is empirically validated.\n",
    )

    report = _evaluate(tmp_path, ledger, "empirical")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_EVIDENCE_BOUNDARY_VIOLATION


def test_safe_textual_repair_can_restore_human_review_readiness(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "repair-ready")
    unsafe = _revised_text(tmp_path, "repair-ready") + (
        "\n[UNSAFE SECTION OMITTED] forbidden label appears in generated prose: Conjecture; "
        "generated prose contains unsupported sentences; the synthetic result is "
        "empirically validated.\n"
    )
    _replace_revised_draft(tmp_path, ledger, "repair-ready", unsafe)
    blocked = _evaluate(tmp_path, ledger, "repair-ready")
    assert blocked.decision.ready_for_human_review is False

    registry = CitationRegistry.model_validate_json(
        (tmp_path / "runs/repair-ready/reports/citation-registry.json").read_text(
            encoding="utf-8"
        )
    )
    critic = critique_generated_paper(
        run_id="repair-ready",
        markdown=unsafe,
        citation_registry=registry,
    )
    repaired = apply_safe_fake_revision(
        run_id="repair-ready",
        markdown=unsafe,
        revision_plan=build_paper_revision_plan(critic),
        citation_registry=registry,
        bounded_text_repair=True,
    )
    _replace_revised_draft(
        tmp_path,
        ledger,
        "repair-ready",
        repaired.revised_markdown,
    )

    ready = _evaluate(tmp_path, ledger, "repair-ready")
    assert ready.decision.status in {
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
    }


def test_release_gate_fails_when_missing_citations_remain_unresolved(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "missing-claim-support")
    _replace_claim_support_counts(
        tmp_path,
        ledger,
        "missing-claim-support",
        missing_required_citation=1,
    )

    report = _evaluate(tmp_path, ledger, "missing-claim-support")

    assert report.decision.status == FullPaperReleaseStatus.RELEASE_GATE_FAILED
    assert report.decision.ready_for_human_review is False
    assert report.publication_ready is False


def test_release_gate_passes_after_post_repair_claim_support_is_clean(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "clean-claim-support")
    _replace_claim_support_counts(
        tmp_path,
        ledger,
        "clean-claim-support",
        missing_required_citation=0,
    )

    report = _evaluate(tmp_path, ledger, "clean-claim-support")

    assert report.decision.status in {
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
    }
    assert report.publication_ready is False


def test_unsupported_leanverified_and_publication_language_block(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "labels")
    _replace_revised_draft(
        tmp_path,
        ledger,
        "labels",
        _revised_text(tmp_path, "labels")
        + "\nThis unsupported statement is LeanVerified and publication ready.\n",
    )

    report = _evaluate(tmp_path, ledger, "labels")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_EVIDENCE_BOUNDARY_VIOLATION
    assert "LeanVerified" in report.evidence_boundary.unsupported_labels
    assert any("publication readiness" in reason for reason in report.evidence_boundary.reasons)


def test_hash_mismatch_blocks_as_inconsistent_provenance(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "hash")
    path = tmp_path / "runs/hash/latex/revised-paper.tex"
    path.write_text(path.read_text(encoding="utf-8") + "% changed\n", encoding="utf-8")

    report = _evaluate(tmp_path, ledger, "hash")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_INCONSISTENT_PROVENANCE
    assert "revised-paper" in report.completeness.hash_mismatch_artifact_ids


def test_missing_appendices_exceeds_default_critic_threshold(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "appendices")
    markdown = _revised_text(tmp_path, "appendices")
    markdown = markdown.replace("## Claim/Evidence Appendix", "## Omitted Claims")
    markdown = markdown.replace("## Provenance Appendix", "## Omitted Provenance")
    _replace_revised_draft(tmp_path, ledger, "appendices", markdown)

    report = _evaluate(tmp_path, ledger, "appendices")

    assert report.decision.status == FullPaperReleaseStatus.BLOCKED_CRITIC_FINDINGS
    assert report.critic_major_findings > 0


def test_release_gate_does_not_mutate_claim_table_or_labels(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "readonly")
    claim_path = tmp_path / "runs/readonly/reports/claim-table.json"
    before = claim_path.read_bytes()
    commits_before = len(ledger.list_commits("readonly"))

    report = _evaluate(tmp_path, ledger, "readonly")

    assert claim_path.read_bytes() == before
    assert len(ledger.list_commits("readonly")) == commits_before
    assert report.evidence_boundary.creates_or_upgrades_labels is False
    assert report.decision.is_verification_evidence is False


def test_write_report_persists_hashed_non_evidence_artifacts(tmp_path) -> None:
    ledger = _prepare_safe_bundle(tmp_path, "write")
    result = run_full_paper_release_gate(
        run_id="write",
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id="write", write_report=True),
    )

    for ref in (
        result.report_artifact,
        result.completeness_artifact,
        result.evidence_boundary_artifact,
        result.summary_artifact,
    ):
        assert ref is not None
        path = tmp_path / ref.path
        assert ref.content_hash == sha256_file(path)
        linked = ArtifactRef.model_validate_json(
            (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
        )
        assert linked.metadata["is_verification_evidence"] is False
        assert linked.metadata["implies_publication_readiness"] is False
    assert result.report.decision.publication_ready is False


def test_evaluate_paper_release_cli_json_and_write_report(tmp_path) -> None:
    _prepare_safe_bundle(tmp_path, "cli")

    result = CliRunner().invoke(
        app,
        [
            "evaluate-paper-release",
            "--root",
            str(tmp_path),
            "--run-id",
            "cli",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report = payload["full_paper_release_report"]
    assert report["decision"]["ready_for_human_review"] is True
    assert report["publication_ready"] is False
    assert payload["artifacts"]["release_report"] is not None


def _prepare_safe_bundle(tmp_path, run_id: str) -> ResearchLedger:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id=run_id,
            write_report=True,
            apply_safe_fake_revision=True,
            reexport_latex_after_revision=True,
        ),
    )
    return ledger


def _evaluate(tmp_path, ledger: ResearchLedger, run_id: str) -> FullPaperReleaseReport:
    return evaluate_full_paper_release(
        run_id=run_id,
        root=tmp_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id),
    )


def _revised_text(tmp_path, run_id: str) -> str:
    return (tmp_path / "runs" / run_id / "reports" / "revised-manuscript-draft.md").read_text(
        encoding="utf-8"
    )


def _replace_revised_draft(
    tmp_path,
    ledger: ResearchLedger,
    run_id: str,
    markdown: str,
) -> None:
    persist_markdown_artifact(
        run_id=run_id,
        store=ArtifactStore(tmp_path),
        ledger=ledger,
        artifact_id="revised-manuscript-draft",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        action_type=ControllerActionType.PAPER_REVISION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "test_revision": True,
            "is_verification_evidence": False,
        },
        metadata={
            "artifact_role": "paper_revision_presentation_draft",
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _replace_claim_support_counts(
    tmp_path,
    ledger: ResearchLedger,
    run_id: str,
    *,
    missing_required_citation: int,
) -> None:
    path = tmp_path / "runs" / run_id / "reports" / "claim-support-audit.json"
    audit = ClaimSupportAuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    counts = {
        **audit.summary_counts,
        "missing_required_citation": missing_required_citation,
        "scope_mismatch": 0,
        "forbidden_claim": 0,
        "unsupported_external_claim": 0,
        "citation_as_validation_misuse": 0,
    }
    updated = audit.model_copy(
        update={
            "summary_counts": counts,
            "post_adjudication_summary_counts": counts,
            "unsupported_items": (
                audit.unsupported_items if missing_required_citation else []
            ),
        }
    )
    persist_artifacts_with_commit(
        run_id=run_id,
        store=ArtifactStore(tmp_path),
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="claim-support-audit",
                artifact_type=ArtifactType.REPORT,
                payload=updated,
                artifact_format="json",
                metadata={
                    "artifact_role": "claim_support_citation_discipline_context",
                    "is_verification_evidence": False,
                    "creates_scientific_validation": False,
                    "implies_publication_readiness": False,
                },
            )
        ],
        action_type=ControllerActionType.FULL_PAPER_GENERATION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "test_claim_support_update": True,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )
