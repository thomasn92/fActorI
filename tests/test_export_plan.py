from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import (
    ExportPreparationError,
    build_export_claim_map,
    build_export_section_map,
    evaluate_export_readiness,
    load_export_preparation_inputs,
    prepare_export,
)
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.latex_plan import build_latex_export_plan
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.prose_contract import build_prose_generation_contract
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactType,
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    DraftClaimPlaceholder,
    DraftSection,
    DraftSkeleton,
    FinalAuditReport,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    PaperAppendix,
    PaperSection,
    PaperSkeleton,
    ReleaseGateDecision,
    ReleaseGateStatus,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_prepare_export_errors_without_final_audit(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(ExportPreparationError, match="Final audit artifacts not found"):
        load_export_preparation_inputs("run-1", ledger)


def test_export_preparation_loads_inputs_after_final_audit(tmp_path) -> None:
    _, ledger = _run_pipeline_to_final_audit(tmp_path)

    inputs = load_export_preparation_inputs("run-1", ledger)

    assert inputs.paper_skeleton.paper_id
    assert inputs.final_audit_report.checks
    assert inputs.release_gate_decision.audit_checks > 0


def test_section_to_source_mapping_is_deterministic() -> None:
    first = build_export_section_map(_paper_skeleton(), _manuscript_plan(), _draft_skeleton())
    second = build_export_section_map(_paper_skeleton(), _manuscript_plan(), _draft_skeleton())

    assert first == second
    assert first[0].source_plan_section_id == "theory"
    assert first[0].source_draft_section_id == "theory"


def test_claim_to_evidence_mapping_is_deterministic_and_includes_hashes() -> None:
    first = build_export_claim_map(_claim_table(), _artifact_manifest())
    second = build_export_claim_map(_claim_table(), _artifact_manifest())

    assert first == second
    assert first[0].evidence_hashes == {"fake-proof-candidate-a": "0" * 64}
    assert first[0].producing_commit_hashes == {"fake-proof-candidate-a": "1" * 64}


def test_conjecture_cannot_be_exported_as_theorem() -> None:
    claim_map = build_export_claim_map(
        _claim_table(
            _claim(
                "claim-main",
                VerificationLabel.CONJECTURE,
                text="Theorem: this conjecture is proven.",
            )
        ),
        _artifact_manifest(),
    )

    assert not claim_map[0].export_allowed
    assert "Conjecture cannot be exported as theorem" in claim_map[0].blocking_reason


def test_synthetic_verified_cannot_be_exported_as_real_world_validation() -> None:
    claim_map = build_export_claim_map(
        _claim_table(
            _claim(
                "claim-main",
                VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
                evidence_ids=["fake-synthetic-experiment"],
                evidence_types=["experiment"],
                text="Synthetic evidence proves real-world performance.",
            )
        ),
        _artifact_manifest(
            [
                _entry(
                    "fake-synthetic-experiment",
                    "runs/run-1/experiments/fake-synthetic-experiment.json",
                    ArtifactType.EXPERIMENT,
                    is_evidence=True,
                )
            ]
        ),
    )

    assert not claim_map[0].export_allowed
    assert "real-world validation" in claim_map[0].blocking_reason


def test_negative_result_cannot_be_exported_as_positive_evidence() -> None:
    claim_map = build_export_claim_map(
        _claim_table(
            _claim(
                "claim-main",
                VerificationLabel.NEGATIVE_RESULT,
                text="This negative result is positive evidence.",
            )
        ),
        _artifact_manifest(),
    )

    assert not claim_map[0].export_allowed
    assert "positive evidence" in claim_map[0].blocking_reason


def test_blocked_claims_are_excluded_from_normal_body_but_appendix_allowed() -> None:
    paper = _paper_skeleton(blocked=True)
    audit = _audit_report()
    decision = _release_decision()
    contract = build_prose_generation_contract(
        "run-1",
        paper,
        _claim_table(),
        audit,
        decision,
    )

    assert "claim-blocked" in contract.blocked_claims
    assert "claim-blocked" not in contract.allowed_claims


def test_every_exported_main_claim_has_evidence_links() -> None:
    claim_map = build_export_claim_map(_claim_table(), _artifact_manifest())

    assert all(claim.evidence_artifact_ids for claim in claim_map if claim.export_allowed)


def test_markdown_and_latex_are_not_treated_as_verification_evidence() -> None:
    claim_map = build_export_claim_map(
        _claim_table(
            _claim(
                "claim-main",
                VerificationLabel.LEAN_VERIFIED,
                evidence_ids=["paper-md"],
            )
        ),
        _artifact_manifest(
            [
                _entry(
                    "paper-md",
                    "runs/run-1/reports/paper.md",
                    ArtifactType.REPORT,
                    is_evidence=True,
                    is_presentation=True,
                )
            ]
        ),
    )

    assert not claim_map[0].export_allowed
    assert "Markdown or LaTeX" in claim_map[0].blocking_reason
    assert claim_map[0].evidence_hashes == {}


def test_required_disclaimers_are_present_when_fake_validators_were_used() -> None:
    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        _audit_report(),
        _release_decision(),
    )

    assert any("fake deterministic validators" in item for item in contract.required_disclaimers)


def test_ready_for_external_review_is_false() -> None:
    readiness = _readiness_report()

    assert not readiness.ready_for_external_review


def test_final_audit_blocking_failure_blocks_export_readiness() -> None:
    readiness = _readiness_report(audit=_audit_report(blocking=1))

    assert readiness.export_blocked
    assert not readiness.ready_for_polished_prose


def test_release_ready_with_warnings_yields_export_readiness_with_warnings() -> None:
    readiness = _readiness_report(
        decision=_release_decision(
            status=ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS,
            warnings=["deterministic warning"],
        )
    )

    assert not readiness.export_blocked
    assert readiness.warnings == ["deterministic warning"]


def test_forbidden_latex_commands_are_listed() -> None:
    plan = build_latex_export_plan(
        "run-1",
        _paper_skeleton(),
        build_prose_generation_contract(
            "run-1",
            _paper_skeleton(),
            _claim_table(),
            _audit_report(),
            _release_decision(),
        ),
    )

    assert "\\write18" in plan.forbidden_latex_commands
    assert "shell escape dependent commands" in plan.forbidden_latex_commands


def test_prepare_export_does_not_generate_latex_or_polished_prose(tmp_path) -> None:
    store, ledger = _run_pipeline_to_final_audit(tmp_path)

    prepare_export(run_id="run-1", store=store, ledger=ledger)

    run_path = tmp_path / "runs" / "run-1"
    assert not list(run_path.rglob("*.tex"))
    assert not [path for path in run_path.rglob("*") if "polished" in path.name]


def test_prepare_export_writes_hashed_artifacts_and_commits(tmp_path) -> None:
    store, ledger = _run_pipeline_to_final_audit(tmp_path)

    result = prepare_export(run_id="run-1", store=store, ledger=ledger)
    artifacts = [
        result.prose_contract_artifact,
        result.latex_plan_artifact,
        result.section_map_artifact,
        result.claim_map_artifact,
        result.readiness_json_artifact,
        result.readiness_markdown_artifact,
        result.bundle_manifest_artifact,
    ]
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]

    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)
    assert ControllerActionType.EXPORT_PREPARATION_STARTED in action_types
    assert ControllerActionType.EXPORT_BUNDLE_MANIFEST_WRITTEN in action_types


def test_cli_prepare_export_works_after_full_flow(tmp_path) -> None:
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
        ["prepare-export", "--root", str(tmp_path), "--run-id", "run-1"],
    ]
    results = [runner.invoke(app, command) for command in commands]

    assert all(result.exit_code == 0 for result in results)
    assert "sections=" in results[-1].output
    assert "ready_for_external_review=false" in results[-1].output
    assert (
        "export_readiness_report=runs/run-1/reports/export-readiness-report.md"
        in results[-1].output
    )


def test_cli_prepare_export_errors_without_final_audit(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-export", "--root", str(tmp_path), "--run-id", "run-1"])

    assert result.exit_code == 1
    assert "Final audit artifacts not found" in result.stderr


def _run_pipeline_to_final_audit(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    run_final_audit(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _readiness_report(
    *,
    audit: FinalAuditReport | None = None,
    decision: ReleaseGateDecision | None = None,
):
    audit = audit or _audit_report()
    decision = decision or _release_decision()
    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        audit,
        decision,
    )
    latex_plan = build_latex_export_plan("run-1", _paper_skeleton(), contract)
    return evaluate_export_readiness(
        contract,
        latex_plan,
        audit,
        decision,
        build_export_claim_map(_claim_table(), _artifact_manifest()),
    )


def _paper_skeleton(*, blocked: bool = False) -> PaperSkeleton:
    placeholder = DraftClaimPlaceholder(
        claim_id="claim-main",
        candidate_id="candidate-a",
        claim_label=VerificationLabel.LEAN_VERIFIED,
        placeholder_text="[LeanVerified placeholder]",
        evidence_artifact_ids=["fake-proof-candidate-a"],
        allowed_section="Theory",
    )
    return PaperSkeleton(
        paper_id="paper",
        run_id="run-1",
        title="Paper",
        abstract_scaffold="Abstract.",
        sections=[
            PaperSection(
                section_id="theory",
                title="Theory or Synthetic Experiments",
                purpose="Use claim table.",
                claim_placeholders=[placeholder],
                evidence_artifact_ids=["fake-proof-candidate-a"],
            )
        ],
        appendices=[
            PaperAppendix(
                appendix_id="appendix-b",
                title="Appendix B: Blocked or Downgraded Claims",
                content_lines=["claim-blocked: blocked"] if blocked else ["none"],
            )
        ],
        claim_placeholders=[placeholder],
        provenance_refs={},
    )


def _manuscript_plan() -> ManuscriptPlan:
    return ManuscriptPlan(
        plan_id="plan",
        final_nucleus_id="final",
        nucleus_type="BranchNucleus",
        title="Paper",
        sections=[
            ManuscriptSectionPlan(
                section_id="theory",
                title="Theory or Synthetic Experiments",
                bullets=["Use claim table."],
                allowed_claim_ids=["claim-main"],
            )
        ],
        allowed_claim_ids=["claim-main"],
        blocked_claim_ids=[],
    )


def _draft_skeleton() -> DraftSkeleton:
    return DraftSkeleton(
        skeleton_id="draft",
        title="Paper",
        abstract_stub="Abstract.",
        section_stubs=[
            DraftSection(
                section_id="theory",
                section_title="Theory or Synthetic Experiments",
                section_purpose="Use claim table.",
                allowed_claim_ids=["claim-main"],
                paragraph_placeholders=["placeholder"],
            )
        ],
        claim_placeholders=[],
    )


def _claim_table(claim: Claim | None = None) -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[claim or _claim("claim-main", VerificationLabel.LEAN_VERIFIED)],
    )


def _claim(
    claim_id: str,
    label: VerificationLabel,
    *,
    evidence_ids: list[str] | None = None,
    evidence_types: list[str] | None = None,
    text: str = "Deterministic claim.",
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
        reason="test",
    )


def _artifact_manifest(entries: list[ArtifactManifestEntry] | None = None) -> ArtifactManifest:
    return ArtifactManifest(
        run_id="run-1",
        artifacts=entries
        or [
            _entry(
                "fake-proof-candidate-a",
                "runs/run-1/lean/fake-proof-candidate-a.json",
                ArtifactType.LEAN,
                is_evidence=True,
            )
        ],
        evidence_artifact_count=1,
        presentation_artifact_count=0,
    )


def _entry(
    artifact_id: str,
    path: str,
    artifact_type: ArtifactType,
    *,
    is_evidence: bool,
    is_presentation: bool = False,
) -> ArtifactManifestEntry:
    return ArtifactManifestEntry(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        path=path,
        content_hash="0" * 64,
        producing_commit_hash="1" * 64,
        is_evidence=is_evidence,
        is_presentation=is_presentation,
    )


def _audit_report(*, blocking: int = 0) -> FinalAuditReport:
    check = AuditCheck(
        check_id="paper_required_sections",
        category=AuditCategory.PAPER_SKELETON_CONSISTENCY,
        status=AuditCheckStatus.FAIL if blocking else AuditCheckStatus.PASS,
        severity=AuditSeverity.BLOCKING if blocking else AuditSeverity.INFO,
        message="blocking" if blocking else "ok",
    )
    return FinalAuditReport(
        run_id="run-1",
        checks=[check],
        passes_count=0 if blocking else 1,
        warnings_count=0,
        failures_count=blocking,
        blocking_failures_count=blocking,
    )


def _release_decision(
    *,
    status: ReleaseGateStatus = ReleaseGateStatus.RELEASE_READY,
    warnings: list[str] | None = None,
) -> ReleaseGateDecision:
    return ReleaseGateDecision(
        run_id="run-1",
        status=status,
        ready_for_polished_prose=True,
        ready_for_latex_export=True,
        ready_for_external_review=False,
        warnings=warnings or [],
        audit_checks=1,
    )
