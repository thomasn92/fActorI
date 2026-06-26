from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import AdapterExternalCallsDisabled, AdapterMissingCredentials
from factori.adapters.fake import FakeProseGenerator
from factori.adapters.registry import get_adapter_registry
from factori.cli import app
from factori.manuscript_drafting import (
    build_manuscript_drafting_plan,
    draft_manuscript,
    draft_manuscript_sections,
)
from factori.prose_contract import build_prose_evidence_map
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Claim,
    ClaimEvidenceLink,
    ClaimTable,
    GeneratedSectionDraft,
    ManuscriptDraftingPlan,
    ManuscriptDraftStatus,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    PaperShapeCritique,
    PipelineRunConfig,
    PipelineStage,
    ProseSectionContract,
    VerificationLabel,
)


def test_fake_prose_backend_remains_default_for_manuscript_drafting() -> None:
    registry = get_adapter_registry(AdapterConfig())

    assert registry.config.prose_backend == "fake"
    assert isinstance(registry.prose_generator, FakeProseGenerator)


def test_section_drafting_plan_and_tasks_are_deterministic() -> None:
    first = _drafting_plan()
    second = _drafting_plan()

    assert first == second
    assert [task.section_id for task in first.tasks] == ["introduction", "results"]
    assert first.tasks[0].allowed_claim_ids == ["claim-main"]
    assert first.tasks[0].allowed_evidence_artifact_ids == ["evidence-a"]


def test_each_section_contract_contains_allowed_claims_and_evidence() -> None:
    task = _drafting_plan().tasks[0]

    assert task.prose_contract.allowed_claim_ids == ["claim-main"]
    assert task.prose_contract.allowed_evidence_artifact_ids == ["evidence-a"]
    assert task.prose_contract.is_verification_evidence is False


def test_fake_manuscript_section_drafting_applies_safety_to_every_section() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(),
        claim_table=_claim_table(),
        narrative_contract=_narrative_contract(),
        prose_generator=FakeProseGenerator(),
    )

    assert len(results) == 2
    assert all(result.safety_report is not None for result in results)
    assert all(result.safe for result in results)
    assert all(not result.is_verification_evidence for result in results)


def test_unsafe_section_is_marked_unsafe_for_unknown_claim_and_evidence() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(),
        claim_table=_claim_table(),
        narrative_contract=_narrative_contract(),
        prose_generator=UnsafeProseGenerator(claim_id="claim-unknown", evidence_id="evidence-x"),
    )

    assert results[0].rejected
    assert not results[0].safe
    assert any("claim IDs" in reason for reason in results[0].safety_reasons)
    assert any("evidence artifact IDs" in reason for reason in results[0].safety_reasons)


def test_unsafe_section_is_marked_unsafe_for_invented_citation() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(),
        claim_table=_claim_table(),
        narrative_contract=_narrative_contract(),
        prose_generator=UnsafeProseGenerator(citation_id="citation-invented"),
    )

    assert results[0].rejected
    assert any("citation IDs" in reason for reason in results[0].safety_reasons)


def test_lean_verified_prose_is_rejected_without_linked_proof_label() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(),
        claim_table=_claim_table(label=VerificationLabel.CONJECTURE),
        narrative_contract=_narrative_contract(),
        prose_generator=UnsafeProseGenerator(
            content="This paragraph claims LeanVerified status for the result."
        ),
    )

    assert results[0].rejected
    assert any("LeanVerified" in reason for reason in results[0].safety_reasons)


def test_synthetic_experiment_verified_prose_requires_linked_synthetic_label() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(),
        claim_table=_claim_table(label=VerificationLabel.CONJECTURE),
        narrative_contract=_narrative_contract(),
        prose_generator=UnsafeProseGenerator(
            content="This paragraph claims SyntheticExperimentVerified status."
        ),
    )

    assert results[0].rejected
    assert any("SyntheticExperimentVerified" in reason for reason in results[0].safety_reasons)


def test_real_world_validation_from_synthetic_evidence_is_rejected() -> None:
    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(
            claim_table=_claim_table(label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED)
        ),
        claim_table=_claim_table(label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED),
        narrative_contract=_narrative_contract(),
        prose_generator=UnsafeProseGenerator(
            content="SyntheticExperimentVerified evidence gives real-world validation."
        ),
    )

    assert results[0].rejected
    assert any("real-world validation" in reason for reason in results[0].safety_reasons)


def test_draft_manuscript_cli_works_with_fake_backend_and_json(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-1",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "draft-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["drafting_report"]["draft_status"] in {
        ManuscriptDraftStatus.DRAFT_COMPLETE.value,
        ManuscriptDraftStatus.DRAFT_COMPLETE_WITH_WARNINGS.value,
    }
    assert payload["complete_draft"]["is_verification_evidence"] is False
    assert "## Claim/Evidence Appendix" in payload["complete_draft"]["markdown"]


def test_draft_manuscript_write_report_writes_content_hashed_artifacts(tmp_path) -> None:
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
            "draft-manuscript",
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
    artifacts = payload["artifacts"]
    for key in ("plan", "drafting_report", "complete_draft", "assembly_report"):
        ref = ArtifactRef.model_validate(artifacts[key])
        assert len(ref.content_hash) == 64
        assert (tmp_path / ref.path).is_file()
        linked = ArtifactRef.model_validate_json(
            (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
        )
        assert linked.metadata["is_verification_evidence"] is False
        assert linked.metadata["creates_scientific_validation"] is False


def test_draft_manuscript_real_backend_fails_without_external_call_opt_in(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "draft-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--prose-backend",
            "openai",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr


def test_draft_manuscript_real_backend_fails_without_api_key() -> None:
    with pytest.raises(AdapterMissingCredentials):
        get_adapter_registry(
            AdapterConfig(prose_backend="openai", allow_external_calls=True),
            environ={},
        )


def test_draft_manuscript_real_backend_with_injected_transport_uses_no_network(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-3",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    registry = get_adapter_registry(
        AdapterConfig(
            prose_backend="openai",
            allow_external_calls=True,
            prose_api_key="test-key",
            prose_model="test-model",
        ),
        prose_transport=MirroringProseTransport(),
    )

    result = draft_manuscript(
        run_id="run-3",
        store=__import__("factori.artifacts").artifacts.ArtifactStore(tmp_path),
        ledger=__import__("factori.ledger").ledger.ResearchLedger(
            tmp_path / "runs" / "run-3" / "ledger.sqlite"
        ),
        prose_generator=registry.prose_generator,
    )

    assert result.drafting_report.sections_total == len(result.drafting_plan.tasks)
    assert result.drafting_report.sections_unsafe == 0
    assert registry.prose_generator.raw_responses


def test_generated_markdown_does_not_mutate_claim_table_or_evidence_map(tmp_path) -> None:
    claim_table = _claim_table()
    before = claim_table.model_dump(mode="json")
    evidence_before = build_prose_evidence_map(claim_table)

    results = draft_manuscript_sections(
        drafting_plan=_drafting_plan(claim_table=claim_table),
        claim_table=claim_table,
        narrative_contract=_narrative_contract(),
        prose_generator=FakeProseGenerator(),
    )

    assert results
    assert claim_table.model_dump(mode="json") == before
    assert build_prose_evidence_map(claim_table) == evidence_before


def test_openai_backend_requires_opt_in_type_for_engine() -> None:
    with pytest.raises(AdapterExternalCallsDisabled):
        get_adapter_registry(AdapterConfig(prose_backend="openai"))


class UnsafeProseGenerator:
    backend_name = "fake"
    is_fake = True

    def __init__(
        self,
        *,
        claim_id: str = "claim-main",
        evidence_id: str = "evidence-a",
        citation_id: str | None = None,
        content: str = "This unsafe section uses a generated claim.",
    ) -> None:
        self.claim_id = claim_id
        self.evidence_id = evidence_id
        self.citation_id = citation_id
        self.content = content

    def generate_section(
        self,
        section_contract: ProseSectionContract,
        claim_table: ClaimTable,
    ) -> GeneratedSectionDraft:
        del claim_table
        return GeneratedSectionDraft(
            section_id=section_contract.section_id,
            title=section_contract.section_title,
            content=self.content,
            claim_ids=[self.claim_id],
            used_claim_ids=[self.claim_id],
            used_evidence_artifact_ids=[self.evidence_id],
            used_citation_ids=[self.citation_id] if self.citation_id else [],
            unsupported_sentences=[],
            warnings=[],
        )


class MirroringProseTransport:
    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del api_key, model, response_schema
        payload = json.loads(prompt)
        section = payload["section_contract"]
        return {
            "section_id": section["section_id"],
            "title": section["section_title"],
            "draft_markdown": (
                "[FAKE PROSE DRAFT] "
                f"section_stub={len(section['section_id'])}; "
                f"claim_count={len(section['allowed_claim_ids'])}; "
                f"evidence_count={len(section['allowed_evidence_artifact_ids'])}; "
                "No scientific label is created or upgraded."
            ),
            "used_claim_ids": section["allowed_claim_ids"],
            "used_evidence_artifact_ids": section["allowed_evidence_artifact_ids"],
            "used_citation_ids": [],
            "unsupported_sentences": [],
            "warnings": ["Fake prose draft is a deterministic placeholder."],
        }


def _drafting_plan(claim_table: ClaimTable | None = None) -> ManuscriptDraftingPlan:
    claim_table = claim_table or _claim_table()
    return build_manuscript_drafting_plan(
        run_id="run-1",
        manuscript_plan=_manuscript_plan(),
        claim_table=claim_table,
        narrative_contract=_narrative_contract(),
        paper_shape_critique=_paper_shape_critique(),
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
                bullets=["Frame the problem."],
                allowed_claim_ids=["claim-main"],
                narrative_roles=[
                    NarrativeSectionRole.PROBLEM_FRAMING,
                    NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
                ],
            ),
            ManuscriptSectionPlan(
                section_id="results",
                title="Results",
                bullets=["State bounded results."],
                allowed_claim_ids=[],
                narrative_roles=[NarrativeSectionRole.MAIN_BODY_RESULT],
            ),
        ],
        allowed_claim_ids=["claim-main"],
        blocked_claim_ids=[],
    )


def _claim_table(label: VerificationLabel = VerificationLabel.CONJECTURE) -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[
            Claim(
                claim_id="claim-main",
                claim_text="The example remains bounded by its label.",
                claim_label=label,
                candidate_id="candidate-a",
                evidence_artifact_ids=["evidence-a"],
                evidence_types=["lean" if label == VerificationLabel.LEAN_VERIFIED else "proof"],
                allowed_in_main_text=True,
                allowed_section="Introduction",
                reason="test",
            )
        ],
        evidence_links=[
            ClaimEvidenceLink(
                claim_id="claim-main",
                artifact_id="evidence-a",
                artifact_type=ArtifactType.LEAN,
                evidence_role="proof",
                supports_label=label == VerificationLabel.LEAN_VERIFIED,
            )
        ],
    )


def _narrative_contract() -> NarrativeManuscriptContract:
    return NarrativeManuscriptContract(
        contract_id="narrative-contract",
        run_id="run-1",
        final_nucleus_id="final",
        central_message="A bounded deterministic example.",
        problem_statement="State the problem.",
        why_interesting="It clarifies deterministic boundaries.",
        literature_gap="Complete coverage is not claimed.",
        novelty_claim="Novelty is bounded by the claim table.",
        model_frame="Use a simple model.",
        notation_policy="Use minimal notation.",
        main_result_id="claim-main",
        main_result_in_words="The example remains bounded by its label.",
        appendix_policy="Move details to the appendix.",
    )


def _paper_shape_critique() -> PaperShapeCritique:
    from factori.paper_shape import critique_paper_shape

    return critique_paper_shape(_narrative_contract(), _manuscript_plan())
