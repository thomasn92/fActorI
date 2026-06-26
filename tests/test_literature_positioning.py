from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.citations import build_citation_registry
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.literature_positioning import (
    NON_EXHAUSTIVENESS_DISCLAIMER,
    build_literature_positioning_contract,
    build_literature_positioning_report,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    BranchStatus,
    Claim,
    ClaimTable,
    ControllerActionType,
    LiteratureGapStatement,
    LiteraturePositioningContract,
    LiteraturePositioningReport,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    PipelineRunConfig,
    PipelineStage,
    RetrievalAdequacyCertificate,
    RetrievalParseReport,
    RetrievalQuery,
    RetrievalResult,
    RetrievalRunReport,
    SourceProvenance,
    VerificationLabel,
)

HASH = "0" * 64


def test_literature_positioning_models_are_importable() -> None:
    assert LiteraturePositioningContract.__name__ == "LiteraturePositioningContract"
    assert LiteratureGapStatement.__name__ == "LiteratureGapStatement"
    assert LiteraturePositioningReport.__name__ == "LiteraturePositioningReport"


def test_literature_positioning_contract_includes_non_exhaustiveness_disclaimer() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])

    contract = build_literature_positioning_contract(
        run_id="run-1",
        narrative_contract=_narrative_contract(),
        citation_registry=registry,
    )

    assert contract.non_exhaustiveness_disclaimer == NON_EXHAUSTIVENESS_DISCLAIMER
    assert "not proof of novelty" in contract.non_exhaustiveness_disclaimer
    assert contract.proves_novelty is False
    assert contract.is_verification_evidence is False


def test_literature_positioning_report_uses_known_citation_markers() -> None:
    registry = build_citation_registry("run-1", [_source("S1")])
    key = registry.citations[0].citation_key

    report = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=_narrative_contract(),
    )

    assert f"[@{key}]" in report.markdown_intro_paragraph
    assert "not exhaustive coverage" in report.literature_limitations_paragraph
    assert not report.proves_novelty
    assert not report.claims_literature_coverage


def test_literature_positioning_report_is_deterministic() -> None:
    registry = build_citation_registry("run-1", [_source("S1"), _source("S2")])

    first = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=_narrative_contract(),
    )
    second = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=_narrative_contract(),
    )

    assert first == second


def test_prose_section_contracts_include_allowed_citation_keys() -> None:
    from factori.manuscript_drafting import build_manuscript_drafting_plan
    from factori.paper_shape import critique_paper_shape

    registry = build_citation_registry("run-1", [_source("S1")])
    narrative = _narrative_contract()
    manuscript_plan = _manuscript_plan()
    report = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=narrative,
    )

    plan = build_manuscript_drafting_plan(
        run_id="run-1",
        manuscript_plan=manuscript_plan,
        claim_table=_claim_table(),
        narrative_contract=narrative,
        paper_shape_critique=critique_paper_shape(narrative, manuscript_plan),
        citation_registry=registry,
        literature_positioning_report=report,
    )

    intro = plan.tasks[0]
    assert intro.allowed_citation_keys == [registry.citations[0].citation_key]
    assert intro.prose_contract.literature_positioning_context is not None


def test_draft_manuscript_can_include_citation_markers_when_registry_exists(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-1",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    _append_retrieval_run(tmp_path, "run-1")

    result = CliRunner().invoke(
        app,
        [
            "draft-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--include-citations",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    markdown = payload["complete_draft"]["markdown"]
    citation_key = payload["citation_registry"]["citations"][0]["citation_key"]
    assert f"[@{citation_key}]" in markdown
    assert "## Bibliography" in markdown
    assert "Literature positioning is bounded" in markdown
    assert payload["citation_safety_report"]["is_verification_evidence"] is False


def test_build_citation_registry_cli_json_and_write_report(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-2",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "build-citation-registry",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-2",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["citation_registry"]["is_verification_evidence"] is False
    assert payload["literature_positioning_report"]["proves_novelty"] is False
    for key in ("citation_registry", "literature_positioning", "citation_safety"):
        artifact = payload["artifacts"][key]
        assert (tmp_path / artifact["path"]).is_file()


def test_build_citation_registry_cli_works_without_write_report(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-3",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "build-citation-registry",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-3",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "proves_novelty=false" in result.output


def test_citation_artifacts_are_not_novelty_proof_or_evidence(tmp_path) -> None:
    del tmp_path
    registry = build_citation_registry("run-1", [_source("S1")])
    report = build_literature_positioning_report(
        run_id="run-1",
        citation_registry=registry,
        narrative_contract=_narrative_contract(),
    )

    assert not registry.is_verification_evidence
    assert not registry.proves_novelty
    assert not report.is_verification_evidence
    assert not report.proves_novelty


def _source(source_id: str) -> RetrievalResult:
    provenance = SourceProvenance(
        source_id=source_id,
        provider="fake",
        query="bounded retrieval context",
        rank=0,
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash=HASH,
    )
    return RetrievalResult(
        source_id=source_id,
        title="Bounded retrieval context",
        authors=["Ada Smith"],
        year=2024,
        provider="fake",
        retrieved_at=provenance.retrieved_at,
        query=provenance.query,
        rank=0,
        raw_metadata_hash=HASH,
        source_provenance=provenance,
        fake=True,
    )


def _narrative_contract() -> NarrativeManuscriptContract:
    return NarrativeManuscriptContract(
        contract_id="narrative-contract",
        run_id="run-1",
        central_message="A bounded deterministic example.",
        problem_statement="State the problem.",
        literature_gap="The literature gap is bounded by source metadata.",
        novelty_claim="Novelty remains a bounded manuscript claim.",
    )


def _append_retrieval_run(root, run_id: str) -> None:
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    result = _source("S1")
    report = RetrievalRunReport(
        query=RetrievalQuery(
            query_id="query-example",
            query="bounded retrieval context",
            provider="fake",
            limit=1,
            endpoint="fake://retrieval",
            requires_credentials=False,
            fake=True,
        ),
        results=[result],
        parse_report=RetrievalParseReport(
            provider="fake",
            raw_response_hash=HASH,
            accepted_source_ids=[result.source_id],
            fake=True,
        ),
        certificate=RetrievalAdequacyCertificate(
            semantic=0.8,
            keyword=0.8,
            citation=0.8,
            diversity=0.8,
            adversarial=0.8,
            weights={
                "semantic": 0.2,
                "keyword": 0.2,
                "citation": 0.2,
                "diversity": 0.2,
                "adversarial": 0.2,
            },
            rho_adequacy=0.8,
            tau_adequacy=0.8,
            passed=True,
            status=BranchStatus.ACTIVE,
            fake=True,
        ),
        backend="fake",
        provider="fake",
        fake=True,
    )
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        payload=report.model_dump(mode="json"),
    )


def _manuscript_plan() -> ManuscriptPlan:
    return ManuscriptPlan(
        plan_id="manuscript-plan-final",
        final_nucleus_id="final",
        nucleus_type="BranchNucleus",
        title="Deterministic Test Manuscript",
        sections=[
            ManuscriptSectionPlan(
                section_id="introduction",
                title="Introduction",
                bullets=["Frame literature context."],
                allowed_claim_ids=["claim-main"],
                narrative_roles=[
                    NarrativeSectionRole.PROBLEM_FRAMING,
                    NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
                ],
            )
        ],
        allowed_claim_ids=["claim-main"],
        blocked_claim_ids=[],
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[
            Claim(
                claim_id="claim-main",
                claim_text="The example remains bounded by its label.",
                claim_label=VerificationLabel.CONJECTURE,
                candidate_id="candidate-a",
                evidence_artifact_ids=[],
                evidence_types=[],
                allowed_in_main_text=True,
                allowed_section="Introduction",
                reason="test",
            )
        ],
    )
