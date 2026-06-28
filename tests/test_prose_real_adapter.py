from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import AdapterExternalCallsDisabled, AdapterMissingCredentials
from factori.adapters.fake import FakeProseGenerator
from factori.adapters.prose_prompts import build_prose_section_prompt
from factori.adapters.prose_real import OpenAIProseGenerator
from factori.adapters.registry import get_adapter_registry
from factori.cli import app
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    Claim,
    ClaimTable,
    GeneratedSectionDraft,
    NarrativeManuscriptContract,
    PipelineRunConfig,
    PipelineStage,
    ProseSectionContract,
    VerificationLabel,
)


def test_fake_prose_backend_remains_default() -> None:
    config = AdapterConfig()
    registry = get_adapter_registry(config)

    assert config.prose_backend == "fake"
    assert isinstance(registry.prose_generator, FakeProseGenerator)
    assert registry.prose_generator.backend_name == "fake"


def test_fake_prose_generation_is_deterministic() -> None:
    generator = FakeProseGenerator()

    first = generator.generate_section(_section_contract(), _claim_table())
    second = generator.generate_section(_section_contract(), _claim_table())

    assert first == second
    assert "[FAKE PROSE DRAFT]" in first.content
    assert not first.is_verification_evidence
    assert not first.polished


def test_real_prose_backend_fails_when_external_calls_are_disabled() -> None:
    with pytest.raises(AdapterExternalCallsDisabled, match="External calls are disabled"):
        get_adapter_registry(AdapterConfig(prose_backend="openai"))


def test_real_prose_backend_fails_when_api_key_is_missing() -> None:
    with pytest.raises(AdapterMissingCredentials, match="no API key"):
        get_adapter_registry(
            AdapterConfig(prose_backend="openai", allow_external_calls=True),
            environ={},
        )


def test_real_prose_backend_uses_injected_transport_without_network() -> None:
    transport = RecordingProseTransport(_raw_response())
    registry = get_adapter_registry(
        AdapterConfig(
            prose_backend="openai",
            allow_external_calls=True,
            prose_api_key="test-key",
            prose_model="test-model",
        ),
        prose_transport=transport,
    )
    prompt = build_prose_section_prompt(
        _section_contract(),
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
        backend="openai",
        provider="openai",
    )

    result = registry.prose_generator.generate_section_from_prompt(prompt)

    assert transport.calls == 1
    assert transport.observed_api_key == "test-key"
    assert result.section_draft is not None
    assert result.section_draft.used_claim_ids == ["claim-main"]
    assert not result.is_verification_evidence


def test_openai_prose_generator_direct_entry_point_uses_gated_transport() -> None:
    generator = OpenAIProseGenerator(
        api_key="test-key",
        model="test-model",
        transport=RecordingProseTransport(_raw_response()),
        allow_external_calls=True,
    )

    draft = generator.generate_section(_section_contract(), _claim_table())

    assert draft.section_id == "introduction"
    assert draft.used_evidence_artifact_ids == ["evidence-a"]
    assert not draft.is_verification_evidence


def test_adapter_registry_exposes_prose_capability_descriptor() -> None:
    registry = get_adapter_registry(AdapterConfig())
    descriptor = next(
        item
        for item in registry.descriptor.providers
        if item.adapter_kind == "prose" and item.backend_name == "openai"
    )

    assert descriptor.supports_prose_generation
    assert descriptor.requires_external_calls
    assert descriptor.requires_api_key
    assert registry.descriptor.active_prose_backend == "fake"


def test_show_adapters_displays_prose_capability() -> None:
    result = CliRunner().invoke(app, ["show-adapters"])

    assert result.exit_code == 0, result.output
    assert "prose_backend=fake" in result.output
    assert "supports_prose_generation=true" in result.output


def test_generate_section_draft_cli_works_with_fake_backend_and_json(tmp_path) -> None:
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
            "generate-section-draft",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--section-id",
            "introduction",
            "--json",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["section_contract"]["section_id"] == "introduction-and-problem-framing"
    assert payload["draft"]["is_verification_evidence"] is False
    assert payload["safety_report"]["is_verification_evidence"] is False
    assert payload["safety_report"]["creates_scientific_validation"] is False
    draft_ref = ArtifactRef.model_validate(payload["artifacts"]["draft"])
    safety_ref = ArtifactRef.model_validate(payload["artifacts"]["safety"])
    assert len(draft_ref.content_hash) == 64
    assert len(safety_ref.content_hash) == 64
    assert (tmp_path / draft_ref.path).is_file()
    assert (tmp_path / safety_ref.path).is_file()
    draft_meta = ArtifactRef.model_validate_json(
        (tmp_path / f"{draft_ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert draft_meta.metadata["is_verification_evidence"] is False
    assert draft_meta.metadata["creates_scientific_validation"] is False


def test_generate_section_draft_cli_rejects_real_backend_without_opt_in(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate-section-draft",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--section-id",
            "introduction",
            "--prose-backend",
            "openai",
        ],
    )

    assert result.exit_code == 1
    assert "External calls are disabled" in result.stderr


class RecordingProseTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls = 0
        self.observed_api_key: str | None = None

    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del model, prompt, response_schema
        self.calls += 1
        self.observed_api_key = api_key
        return self.response


def _raw_response() -> dict[str, object]:
    return {
        "section_id": "introduction",
        "title": "Introduction",
        "draft_markdown": "This section references claim-main using evidence-a.",
        "used_claim_ids": ["claim-main"],
        "used_evidence_artifact_ids": ["evidence-a"],
        "used_citation_ids": [],
        "unsupported_sentences": [],
        "warnings": [],
    }


def _section_contract() -> ProseSectionContract:
    return ProseSectionContract(
        run_id="run-1",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=["claim-main"],
        allowed_evidence_artifact_ids=["evidence-a"],
        allowed_citation_ids=[],
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
        evidence_boundary_instructions=["Generated prose is not evidence."],
        style_instructions=["Use placeholder-grade prose."],
        max_words=120,
        source_contract_hashes={"claim_table": "0" * 64},
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
                evidence_artifact_ids=["evidence-a"],
                evidence_types=["proof"],
                allowed_in_main_text=True,
                allowed_section="Introduction",
                reason="test",
            )
        ],
    )


def _evidence_map() -> dict[str, dict[str, object]]:
    return {
        "evidence-a": {
            "artifact_id": "evidence-a",
            "claim_id": "claim-main",
            "is_verification_evidence": False,
        }
    }


def _narrative_contract() -> NarrativeManuscriptContract:
    return NarrativeManuscriptContract(
        contract_id="narrative",
        run_id="run-1",
        central_message="A bounded deterministic example.",
        problem_statement="State the scoped problem.",
        section_plan=[{"section_id": "introduction", "role": "problem framing"}],
    )


def test_generated_section_type_keeps_prose_out_of_evidence() -> None:
    draft = GeneratedSectionDraft(
        section_id="introduction",
        title="Introduction",
        content="[FAKE PROSE DRAFT] No scientific label is upgraded.",
    )

    assert not draft.is_verification_evidence
    assert draft.fake
