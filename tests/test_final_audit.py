from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.final_audit import (
    FinalAuditError,
    FinalAuditInputs,
    build_final_audit_report,
    load_final_audit_inputs,
    run_final_audit,
)
from factori.final_paper import run_paper_assembly
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    AuditCheckStatus,
    BlockedClaim,
    BranchOutcomeSummary,
    BranchStatus,
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    DraftClaimPlaceholder,
    DraftSection,
    DraftSkeleton,
    FinalNucleus,
    FinalNucleusType,
    LedgerSummary,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    PaperAppendix,
    PaperSection,
    PaperSkeleton,
    ReleaseGateStatus,
    ReproducibilityManifest,
    ResearchObject,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_final_audit_errors_without_paper_skeleton(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(FinalAuditError, match="Paper skeleton artifacts not found"):
        load_final_audit_inputs("run-1", ledger)


def test_final_audit_loads_required_inputs(tmp_path) -> None:
    _, ledger = _run_pipeline_to_paper(tmp_path)

    inputs = load_final_audit_inputs("run-1", ledger)

    assert inputs.paper_skeleton.paper_id
    assert inputs.research_object.final_nucleus.id
    assert inputs.claim_table.claims
    assert inputs.artifact_manifest.artifacts
    assert inputs.ledger_summary.commit_count > 0


def test_final_audit_is_deterministic() -> None:
    inputs = _audit_inputs()

    first = build_final_audit_report(run_id="run-1", inputs=inputs)
    second = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert first == second


def test_missing_required_artifact_produces_blocking_failure() -> None:
    inputs = _audit_inputs()
    research_object = inputs.research_object.model_copy(update={"stage_reports": {}})
    report = build_final_audit_report(
        run_id="run-1",
        inputs=_replace(inputs, research_object=research_object),
    )

    check = _check(report, "required_stage_reports_exist")

    assert check.status == AuditCheckStatus.FAIL
    assert report.blocking_failures_count > 0


def test_missing_artifact_hash_produces_blocking_failure() -> None:
    inputs = _audit_inputs()
    bad_entry = _manifest_entry("runs/run-1/lean/fake-proof.json", content_hash=None)
    manifest = inputs.artifact_manifest.model_copy(update={"artifacts": [bad_entry]})
    report = build_final_audit_report(
        run_id="run-1",
        inputs=_replace(inputs, artifact_manifest=manifest),
    )

    assert _check(report, "listed_artifacts_have_hashes").status == AuditCheckStatus.FAIL


def test_evidence_without_producing_commit_produces_blocking_failure() -> None:
    inputs = _audit_inputs()
    bad_entry = _manifest_entry(
        "runs/run-1/lean/fake-proof.json",
        is_evidence=True,
        producing_commit_hash=None,
    )
    manifest = inputs.artifact_manifest.model_copy(update={"artifacts": [bad_entry]})
    report = build_final_audit_report(
        run_id="run-1",
        inputs=_replace(inputs, artifact_manifest=manifest),
    )

    assert (
        _check(report, "evidence_artifacts_have_producing_commits").status
        == AuditCheckStatus.FAIL
    )


def test_markdown_or_latex_as_verification_evidence_produces_blocking_failure() -> None:
    inputs = _audit_inputs()
    bad_entry = _manifest_entry(
        "runs/run-1/reports/paper.md",
        artifact_type=ArtifactType.REPORT,
        is_evidence=True,
        is_presentation=True,
    )
    manifest = inputs.artifact_manifest.model_copy(update={"artifacts": [bad_entry]})
    report = build_final_audit_report(
        run_id="run-1",
        inputs=_replace(inputs, artifact_manifest=manifest),
    )

    assert (
        _check(report, "markdown_latex_not_verification_evidence").status
        == AuditCheckStatus.FAIL
    )
    assert _check(report, "presentation_artifacts_not_evidence").status == AuditCheckStatus.FAIL


def test_unsupported_main_claim_produces_blocking_failure() -> None:
    inputs = _audit_inputs(
        claim=_claim("claim-main", VerificationLabel.UNSUPPORTED, evidence_ids=["evidence-1"])
    )

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert _check(report, "main_claims_have_evidence_links").status == AuditCheckStatus.PASS
    assert _check(report, "claim_labels_preserved").status == AuditCheckStatus.PASS
    assert report.blocking_failures_count > 0


def test_conjecture_upgraded_to_theorem_produces_blocking_failure() -> None:
    inputs = _audit_inputs(
        claim=_claim(
            "claim-main",
            VerificationLabel.CONJECTURE,
            text="Theorem: this conjecture is proven.",
        )
    )

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert _check(report, "conjectures_not_upgraded").status == AuditCheckStatus.FAIL


def test_synthetic_real_world_claim_produces_blocking_failure() -> None:
    inputs = _audit_inputs(
        claim=_claim(
            "claim-main",
            VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
            evidence_ids=["fake-synthetic-experiment"],
            evidence_types=["experiment"],
            text="Synthetic evidence proves real-world performance.",
        )
    )

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert _check(report, "synthetic_not_real_world").status == AuditCheckStatus.FAIL


def test_real_data_experiment_verified_in_mvp_produces_blocking_failure() -> None:
    inputs = _audit_inputs(
        claim=_claim(
            "claim-main",
            VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED,
            evidence_ids=["real-data"],
            evidence_types=["experiment"],
        )
    )

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert _check(report, "no_real_data_verified_mvp").status == AuditCheckStatus.FAIL


def test_blocked_claim_in_main_results_produces_blocking_failure() -> None:
    blocked = BlockedClaim(
        claim_id="claim-main",
        candidate_id="candidate-a",
        claim_text="Blocked claim.",
        claim_label=VerificationLabel.LEAN_VERIFIED,
        blocked_reason="blocked",
    )
    inputs = _audit_inputs(blocked_claims=[blocked])

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert _check(report, "blocked_claims_not_in_main_results").status == AuditCheckStatus.FAIL


def test_missing_failed_deferred_branch_summary_warns() -> None:
    inputs = _replace(_audit_inputs(), branch_outcomes=[])

    report = build_final_audit_report(run_id="run-1", inputs=inputs)

    assert (
        _check(report, "failed_deferred_pruned_branches_represented").status
        == AuditCheckStatus.WARNING
    )


def test_complete_deterministic_run_has_zero_blocking_failures(tmp_path) -> None:
    store, ledger = _run_pipeline_to_paper(tmp_path)

    result = run_final_audit(run_id="run-1", store=store, ledger=ledger)

    assert result.audit_report.blocking_failures_count == 0


def test_complete_deterministic_run_is_release_ready_or_warning_ready(tmp_path) -> None:
    store, ledger = _run_pipeline_to_paper(tmp_path)

    result = run_final_audit(run_id="run-1", store=store, ledger=ledger)

    assert result.release_gate_decision.status in {
        ReleaseGateStatus.RELEASE_READY,
        ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS,
    }
    assert not result.release_gate_decision.ready_for_external_review


def test_final_audit_artifacts_are_created_and_hashed(tmp_path) -> None:
    store, ledger = _run_pipeline_to_paper(tmp_path)

    result = run_final_audit(run_id="run-1", store=store, ledger=ledger)
    artifacts = [
        result.audit_json_artifact,
        result.audit_markdown_artifact,
        result.release_json_artifact,
        result.release_markdown_artifact,
    ]

    assert all((tmp_path / artifact.path).is_file() for artifact in artifacts)
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)


def test_final_audit_creates_ledger_commits(tmp_path) -> None:
    store, ledger = _run_pipeline_to_paper(tmp_path)

    run_final_audit(run_id="run-1", store=store, ledger=ledger)
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]

    assert ControllerActionType.FINAL_AUDIT_STARTED in action_types
    assert ControllerActionType.FINAL_AUDIT_REPORT_WRITTEN in action_types
    assert ControllerActionType.RELEASE_GATE_DECIDED in action_types


def test_cli_final_audit_works_after_full_flow(tmp_path) -> None:
    runner = CliRunner()
    commands = [
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
        ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"],
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
        ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
        ["plan-manuscript", "--root", str(tmp_path), "--run-id", "run-1"],
        ["build-draft-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
        ["package-research-object", "--root", str(tmp_path), "--run-id", "run-1"],
        ["assemble-paper-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
        ["final-audit", "--root", str(tmp_path), "--run-id", "run-1"],
    ]
    results = [runner.invoke(app, command) for command in commands]

    assert all(result.exit_code == 0 for result in results)
    assert "audit_checks=" in results[-1].output
    assert "ready_for_external_review=false" in results[-1].output
    assert "final_audit_report=runs/run-1/reports/final-audit-report.md" in results[-1].output


def test_cli_final_audit_errors_without_paper_skeleton(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["final-audit", "--root", str(tmp_path), "--run-id", "run-1"])

    assert result.exit_code == 1
    assert "Paper skeleton artifacts not found" in result.stderr


def _run_pipeline_to_paper(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)
    build_research_object(run_id="run-1", store=store, ledger=ledger)
    run_paper_assembly(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _audit_inputs(
    *,
    claim: Claim | None = None,
    blocked_claims: list[BlockedClaim] | None = None,
) -> FinalAuditInputs:
    blocked_claims = blocked_claims or []
    claim = claim or _claim("claim-main", VerificationLabel.LEAN_VERIFIED)
    placeholder = DraftClaimPlaceholder(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim_label=claim.claim_label,
        placeholder_text=f"[{claim.claim_label.value} placeholder]",
        evidence_artifact_ids=claim.evidence_artifact_ids,
        allowed_section=claim.allowed_section,
    )
    paper = PaperSkeleton(
        paper_id="paper-skeleton-final",
        run_id="run-1",
        title="Deterministic Paper",
        abstract_scaffold="Abstract scaffold.",
        sections=[
            _section("abstract", "Abstract", []),
            _section("introduction", "Introduction", []),
            _section("problem-setup", "Problem Setup", []),
            _section("theory", "Theory or Synthetic Experiments", [placeholder]),
            _section("results", "Results", []),
            _section("limitations", "Limitations", []),
            _section("conclusion", "Conclusion", []),
        ],
        appendices=[
            PaperAppendix(
                appendix_id="appendix-b-blocked",
                title="Appendix B: Blocked or Downgraded Claims",
                content_lines=[claim.claim_id for claim in blocked_claims] or ["none"],
            ),
            PaperAppendix(
                appendix_id="appendix-d-branch-outcomes",
                title="Appendix D: Failed, Deferred, and Pruned Branches",
                content_lines=["branch_outcomes_ref=runs/run-1/research_object/branch-outcomes.json"],
            ),
        ],
        claim_placeholders=[placeholder],
        provenance_refs={"ledger_summary": _artifact_ref("ledger-summary")},
    )
    research_object = _research_object()
    claim_table = ClaimTable(final_nucleus_id="final-test", claims=[claim])
    return FinalAuditInputs(
        paper_skeleton=paper,
        research_object=research_object,
        claim_table=claim_table,
        artifact_manifest=ArtifactManifest(
            run_id="run-1",
            artifacts=[
                _manifest_entry(
                    "runs/run-1/lean/fake-proof.json",
                    artifact_type=ArtifactType.LEAN,
                    is_evidence=True,
                    is_presentation=False,
                ),
                _manifest_entry(
                    "runs/run-1/reports/paper.md",
                    artifact_type=ArtifactType.REPORT,
                    is_evidence=False,
                    is_presentation=True,
                ),
            ],
            evidence_artifact_count=1,
            presentation_artifact_count=1,
        ),
        ledger_summary=LedgerSummary(
            run_id="run-1",
            commit_count=10,
            root_commit_hash="2" * 64,
            latest_commit_hash="3" * 64,
            action_type_counts={},
            candidate_count=1,
            artifact_count=2,
            verification_decision_count=1,
            human_tail_escalation_count=0,
        ),
        branch_outcomes=[
            BranchOutcomeSummary(
                candidate_id="candidate-deferred",
                outcome=BranchStatus.DEFERRED_REAL_DATA_CANDIDATE.value,
                status=BranchStatus.DEFERRED_REAL_DATA_CANDIDATE,
                action_type=ControllerActionType.STAGE_A_DATA_GATE_DEFERRED,
                reason="test deferred branch",
            )
        ],
        reproducibility_manifest=ReproducibilityManifest(
            run_id="run-1",
            ledger_exists=True,
            root_commit_exists=True,
            latest_commit_exists=True,
            all_artifacts_have_hashes=True,
            all_evidence_artifacts_have_producing_commits=True,
            claim_table_exists=True,
            draft_skeleton_exists=True,
            manuscript_plan_exists=True,
            final_nucleus_exists=True,
            blocked_claims_list_exists=True,
            environment_metadata_present=True,
            reproducible=True,
        ),
        manuscript_plan=ManuscriptPlan(
            plan_id="plan",
            final_nucleus_id="final-test",
            nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
            title="Deterministic Paper",
            sections=[
                ManuscriptSectionPlan(
                    section_id="theory",
                    title="Theory or Synthetic Experiments",
                    bullets=["Use claim table only."],
                    allowed_claim_ids=[claim.claim_id],
                )
            ],
            allowed_claim_ids=[claim.claim_id],
            blocked_claim_ids=[blocked.claim_id for blocked in blocked_claims],
        ),
        draft_skeleton=DraftSkeleton(
            skeleton_id="draft",
            title="Deterministic Paper",
            abstract_stub="Abstract scaffold.",
            section_stubs=[
                DraftSection(
                    section_id="theory",
                    section_title="Theory or Synthetic Experiments",
                    section_purpose="Use claim table only.",
                    allowed_claim_ids=[claim.claim_id],
                    paragraph_placeholders=["placeholder"],
                )
            ],
            claim_placeholders=[placeholder],
        ),
        blocked_claims=blocked_claims,
        commits=[],
    )


def _section(
    section_id: str,
    title: str,
    placeholders: list[DraftClaimPlaceholder],
) -> PaperSection:
    return PaperSection(
        section_id=section_id,
        title=title,
        purpose=f"Purpose for {title}",
        claim_placeholders=placeholders,
        evidence_artifact_ids=[
            evidence_id
            for placeholder in placeholders
            for evidence_id in placeholder.evidence_artifact_ids
        ],
    )


def _claim(
    claim_id: str,
    label: VerificationLabel,
    *,
    evidence_ids: list[str] | None = None,
    evidence_types: list[str] | None = None,
    text: str = "Deterministic labeled claim.",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=text,
        claim_label=label,
        candidate_id="candidate-a",
        evidence_artifact_ids=["fake-proof-candidate-a"] if evidence_ids is None else evidence_ids,
        evidence_types=["lean"] if evidence_types is None else evidence_types,
        allowed_in_main_text=True,
        allowed_section="Theory",
        reason="test claim",
    )


def _research_object() -> ResearchObject:
    ref = _artifact_ref("placeholder")
    return ResearchObject(
        run_id="run-1",
        final_nucleus=FinalNucleus(
            id="final-test",
            nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
            candidate_id="candidate-a",
            supporting_candidate_ids=["candidate-a"],
            labels_by_candidate={"candidate-a": VerificationLabel.LEAN_VERIFIED},
            reason="test final nucleus",
        ),
        manuscript_plan_ref=_artifact_ref("manuscript-plan"),
        draft_skeleton_ref=_artifact_ref("draft-skeleton"),
        claim_table_ref=_artifact_ref("claim-table"),
        blocked_claims_ref=_artifact_ref("blocked-claims"),
        checklist_ref=_artifact_ref("checklist"),
        stage_reports={
            "stage_a": ref,
            "stage_b": ref,
            "stage_c_selection": ref,
            "stage_c_verification": ref,
            "abstract_synthesis": ref,
        },
        artifact_manifest_ref=_artifact_ref("artifact-manifest"),
        ledger_summary_ref=_artifact_ref("ledger-summary"),
        branch_outcomes_ref=_artifact_ref("branch-outcomes"),
        reproducibility_manifest_ref=_artifact_ref("reproducibility-manifest"),
        created_at="1970-01-01T00:00:00.000000Z",
    )


def _artifact_ref(artifact_id: str) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=f"runs/run-1/reports/{artifact_id}.json",
        content_hash="0" * 64,
        producing_commit_hash="1" * 64,
        metadata={"fake": True},
    )


def _manifest_entry(
    path: str,
    *,
    artifact_type: ArtifactType = ArtifactType.REPORT,
    content_hash: str | None = "0" * 64,
    producing_commit_hash: str | None = "1" * 64,
    is_evidence: bool = False,
    is_presentation: bool = False,
) -> ArtifactManifestEntry:
    return ArtifactManifestEntry(
        artifact_id=path.rsplit("/", maxsplit=1)[-1].split(".")[0],
        artifact_type=artifact_type,
        path=path,
        content_hash=content_hash,
        producing_commit_hash=producing_commit_hash,
        is_evidence=is_evidence,
        is_presentation=is_presentation,
    )


def _replace(inputs: FinalAuditInputs, **updates) -> FinalAuditInputs:
    data = {
        "paper_skeleton": inputs.paper_skeleton,
        "research_object": inputs.research_object,
        "claim_table": inputs.claim_table,
        "artifact_manifest": inputs.artifact_manifest,
        "ledger_summary": inputs.ledger_summary,
        "branch_outcomes": inputs.branch_outcomes,
        "reproducibility_manifest": inputs.reproducibility_manifest,
        "manuscript_plan": inputs.manuscript_plan,
        "draft_skeleton": inputs.draft_skeleton,
        "blocked_claims": inputs.blocked_claims,
        "commits": inputs.commits,
    }
    data.update(updates)
    return FinalAuditInputs(**data)


def _check(report, check_id: str):
    return next(check for check in report.checks if check.check_id == check_id)
