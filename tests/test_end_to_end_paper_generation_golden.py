from __future__ import annotations

from pathlib import Path

import pytest

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.evidence import is_proof_evidence, is_synthetic_experiment_evidence
from factori.final_audit import build_final_audit_report, load_final_audit_inputs
from factori.full_paper_generation import FullPaperGenerationError, generate_full_paper
from factori.full_paper_release import run_full_paper_release_gate
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.output_hygiene import inspect_output_hygiene
from factori.protocol_validation import validate_protocol_examples
from factori.protocols import PROTOCOL_VERSION
from factori.release_gate import decide_release_gate
from factori.replay import replay_verify_run
from factori.rerun_policy import validate_ledger_tip
from factori.run_all import run_deterministic_pipeline
from factori.schema_export import require_protocols_current
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationStatus,
    FullPaperReleaseGateConfig,
    FullPaperReleaseStatus,
    LatexSafetyReport,
    LatexSourceMap,
    LedgerTipStatus,
    OutputHygieneStatus,
    PipelineRunConfig,
    PipelineRunStatus,
    ReleaseGateStatus,
    ReplayStatus,
)

GOLDEN_BUNDLE_ARTIFACT_IDS = [
    "citation-registry",
    "citation-safety-report",
    "claim-support-audit",
    "complete-manuscript-draft",
    "full-paper-artifact-bundle",
    "full-paper-generation-report",
    "latex-export-report",
    "latex-safety-report",
    "latex-source-map",
    "literature-positioning-report",
    "manuscript-assembly-report",
    "manuscript-drafting-plan",
    "manuscript-drafting-report",
    "paper",
    "paper-critic-report",
    "paper-revision-plan",
    "paper-revision-result",
    "references",
    "revised-latex-export-report",
    "revised-latex-safety-report",
    "revised-latex-source-map",
    "revised-manuscript-draft",
    "revised-paper",
    "revised-references",
    "revision-safety-report",
]

GOLDEN_LEDGER_SUFFIX = [
    ControllerActionType.PIPELINE_RUN_REPORT_WRITTEN,
    ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN,
    ControllerActionType.LATEX_EXPORT_WRITTEN,
    ControllerActionType.PAPER_CRITIC_REPORT_WRITTEN,
    ControllerActionType.PAPER_REVISION_WRITTEN,
    ControllerActionType.FULL_PAPER_GENERATION_WRITTEN,
    ControllerActionType.FULL_PAPER_RELEASE_EVALUATED,
]


def test_end_to_end_paper_generation_golden(tmp_path) -> None:
    run_id = "golden-paper"
    pipeline_report = run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
        )
    )
    assert pipeline_report.pipeline_status in {
        PipelineRunStatus.PIPELINE_SUCCEEDED,
        PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS,
    }

    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    claim_table_path = tmp_path / "runs" / run_id / "reports" / "claim-table.json"
    claim_table_before = claim_table_path.read_bytes()

    generation = generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id=run_id,
            write_report=True,
            apply_safe_fake_revision=True,
            reexport_latex_after_revision=True,
        ),
    )
    release = run_full_paper_release_gate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
    )

    assert generation.report.generation_status == (
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS
    )
    assert release.report.decision.status == (
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS
    )
    assert release.report.decision.ready_for_human_review is True
    assert release.report.decision.publication_ready is False
    assert release.report.publication_ready is False
    assert release.report.is_verification_evidence is False
    assert release.report.critic_blocking_findings == 0
    assert release.report.critic_major_findings == 0
    assert release.report.critic_warning_findings == 0
    assert release.report.findings == []

    bundle = generation.artifact_bundle
    assert bundle.artifact_ids == GOLDEN_BUNDLE_ARTIFACT_IDS
    assert bundle.is_verification_evidence is False
    assert bundle.creates_scientific_validation is False
    assert bundle.implies_publication_readiness is False

    commits = ledger.list_commits(run_id)
    ledger.validate()
    assert [commit.action_type for commit in commits[-len(GOLDEN_LEDGER_SUFFIX) :]] == (
        GOLDEN_LEDGER_SUFFIX
    )
    tip_report = validate_ledger_tip(run_id, root=tmp_path)
    assert tip_report.status == LedgerTipStatus.VALID
    assert tip_report.blocking_findings == []

    refs = _artifact_index(commits)
    expected_paths = {
        "complete-manuscript-draft": "runs/golden-paper/reports/complete-manuscript-draft.md",
        "citation-registry": "runs/golden-paper/reports/citation-registry.json",
        "claim-support-audit": "runs/golden-paper/reports/claim-support-audit.json",
        "literature-positioning-report": (
            "runs/golden-paper/reports/literature-positioning-report.json"
        ),
        "paper-critic-report": "runs/golden-paper/reports/paper-critic-report.json",
        "paper": "runs/golden-paper/latex/paper.tex",
        "references": "runs/golden-paper/latex/references.bib",
        "latex-source-map": "runs/golden-paper/latex/latex-source-map.json",
        "revised-paper": "runs/golden-paper/latex/revised-paper.tex",
        "revised-latex-source-map": (
            "runs/golden-paper/latex/revised-latex-source-map.json"
        ),
        "full-paper-generation-report": (
            "runs/golden-paper/reports/full-paper-generation-report.json"
        ),
        "full-paper-release-report": (
            "runs/golden-paper/reports/full-paper-release-report.json"
        ),
        "reviewer-bundle-summary": (
            "runs/golden-paper/reports/reviewer-bundle-summary.json"
        ),
        "reviewer-bundle-summary-markdown": (
            "runs/golden-paper/reports/reviewer-bundle-summary.md"
        ),
    }
    for artifact_id, expected_path in expected_paths.items():
        assert refs[artifact_id].path == expected_path

    paper_and_release_ids = set(bundle.artifact_ids) | {
        "full-paper-release-report",
        "full-paper-bundle-completeness",
        "full-paper-evidence-boundary-report",
        "full-paper-release-summary",
        "reviewer-bundle-summary",
        "reviewer-bundle-summary-markdown",
    }
    hash_snapshot = {}
    for artifact_id in sorted(paper_and_release_ids):
        artifact = refs[artifact_id]
        path = tmp_path / artifact.path
        assert path.is_file()
        assert artifact.content_hash == sha256_file(path)
        assert artifact.producing_commit_hash is not None
        assert artifact.metadata["is_verification_evidence"] is False
        assert artifact.type in {ArtifactType.REPORT, ArtifactType.LATEX}
        assert is_proof_evidence(artifact) is False
        assert is_synthetic_experiment_evidence(artifact) is False
        assert artifact.type != ArtifactType.LITERATURE
        hash_snapshot[artifact_id] = artifact.content_hash

    markdown = (tmp_path / refs["revised-manuscript-draft"].path).read_text(
        encoding="utf-8"
    )
    assert "## Claim/Evidence Appendix" in markdown
    assert "## Provenance Appendix" in markdown
    assert "publication ready" not in markdown.lower()

    citation_registry = CitationRegistry.model_validate_json(
        (tmp_path / refs["citation-registry"].path).read_text(encoding="utf-8")
    )
    assert citation_registry.run_id == run_id
    assert len(citation_registry.citations) == 0
    latex_safety = LatexSafetyReport.model_validate_json(
        (tmp_path / refs["revised-latex-safety-report"].path).read_text(encoding="utf-8")
    )
    assert latex_safety.safe is True
    assert latex_safety.rejected is False
    source_map = LatexSourceMap.model_validate_json(
        (tmp_path / refs["revised-latex-source-map"].path).read_text(encoding="utf-8")
    )
    assert source_map.covers_all_major_sections is True
    assert len(source_map.entries) == 9

    replay = replay_verify_run(run_id, tmp_path)
    assert replay.replay_status == ReplayStatus.REPLAY_VERIFIED
    assert replay.blocking_failures_count == 0
    assert replay.ledger_mutated is False
    assert replay.artifact_manifest_mutated is False

    hygiene = inspect_output_hygiene(run_id, tmp_path)
    assert hygiene.hygiene_status == OutputHygieneStatus.CLEAN
    assert hygiene.blocking_findings_count == 0
    assert hygiene.warnings_count == 0
    assert hygiene.findings == []

    audit_inputs = load_final_audit_inputs(run_id, ledger)
    rebuilt_audit = build_final_audit_report(run_id=run_id, inputs=audit_inputs)
    rebuilt_release = decide_release_gate(rebuilt_audit)
    assert rebuilt_audit.blocking_failures_count == 0
    assert rebuilt_audit.certifies_scientific_validity is False
    assert rebuilt_release.status != ReleaseGateStatus.RELEASE_BLOCKED
    assert rebuilt_release.ready_for_external_review is False
    assert rebuilt_release.certifies_scientific_validity is False

    assert claim_table_path.read_bytes() == claim_table_before
    assert release.report.evidence_boundary.claim_table_unchanged is True
    assert release.report.evidence_boundary.evidence_classification_unchanged is True
    assert release.report.evidence_boundary.creates_or_upgrades_labels is False

    after_checks = _artifact_index(ledger.list_commits(run_id))
    assert {
        artifact_id: after_checks[artifact_id].content_hash
        for artifact_id in hash_snapshot
    } == hash_snapshot

    protocol_check = require_protocols_current()
    assert protocol_check.up_to_date is True
    assert PROTOCOL_VERSION == "0.38.0"
    assert len(protocol_check.schema_files) == 196
    examples = validate_protocol_examples()
    assert examples.examples_checked == 43
    assert examples.examples_valid == 43
    assert examples.examples_invalid == 0

    golden_example = FullPaperArtifactBundle.model_validate_json(
        Path("protocols/examples/full-paper-golden-bundle.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert golden_example.run_id == run_id
    assert golden_example.artifact_ids == GOLDEN_BUNDLE_ARTIFACT_IDS

    with pytest.raises(FullPaperGenerationError, match="already exists"):
        generate_full_paper(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            prose_generator=FakeProseGenerator(),
            config=FullPaperGenerationConfig(run_id=run_id, write_report=True),
        )


def _artifact_index(commits) -> dict[str, ArtifactRef]:
    return {
        artifact.id: artifact
        for commit in commits
        for artifact in commit.artifact_refs
    }
