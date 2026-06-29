from __future__ import annotations

from factori.adapters.prose_prompts import (
    build_prose_section_prompt,
    forbidden_labels_for_section,
)
from factori.schemas import (
    Claim,
    ClaimTable,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    ProseSectionContract,
    VerificationLabel,
)


def test_prose_prompt_construction_is_deterministic() -> None:
    first = build_prose_section_prompt(
        _section_contract(),
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
    )
    second = build_prose_section_prompt(
        _section_contract(),
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
    )

    assert first == second


def test_prose_prompt_includes_claim_and_evidence_grounding() -> None:
    prompt = build_prose_section_prompt(
        _section_contract(),
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
    )

    assert prompt.allowed_claims[0]["claim_id"] == "claim-main"
    assert "evidence-a" in prompt.evidence_map
    assert "Use only allowed claim IDs and evidence artifact IDs" in (
        prompt.prompt_text
    )
    assert "Preserve all claim labels exactly" in prompt.prompt_text


def test_prose_prompt_forbids_label_upgrades_and_invented_citations() -> None:
    prompt = build_prose_section_prompt(
        _section_contract(),
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
    )

    assert "Do not upgrade Conjecture" in prompt.prompt_text
    assert "Do not invent bibliography keys or citations" in prompt.prompt_text
    assert "Do not edit the claim table or evidence classification" in prompt.prompt_text
    assert "Do not add Markdown headings" in prompt.prompt_text


def test_prose_prompt_includes_semantic_required_content_items() -> None:
    contract = _section_contract()
    contract = contract.model_copy(
        update={
            "section_title": "Claim and Evidence Boundaries",
            "required_subsections": [
                "Single bounded central contribution",
                "Evidence boundary statement",
            ],
            "style_instructions": [
                "State exactly one bounded central contribution.",
                "Do not add Markdown headings inside the section body.",
            ],
        }
    )
    prompt = build_prose_section_prompt(
        contract,
        _claim_table(),
        _evidence_map(),
        _narrative_contract(),
    )

    assert "Single bounded central contribution" in prompt.prompt_text
    assert "Evidence boundary statement" in prompt.prompt_text
    assert "Do not add Markdown headings inside the section body" in prompt.prompt_text


def test_forbidden_labels_exclude_only_allowed_claim_labels() -> None:
    labels = forbidden_labels_for_section(_section_contract(), _claim_table())

    assert VerificationLabel.CONJECTURE not in labels
    assert VerificationLabel.LEAN_VERIFIED in labels
    assert VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED in labels


def _section_contract(
    *,
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> ProseSectionContract:
    return ProseSectionContract(
        run_id="run-1",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        narrative_role=[
            NarrativeSectionRole.PROBLEM_FRAMING,
            NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
        ],
        allowed_claim_ids=claim_ids or ["claim-main"],
        allowed_evidence_artifact_ids=evidence_ids or ["evidence-a"],
        allowed_citation_ids=["source-a"],
        forbidden_claims=["claim-blocked"],
        forbidden_labels=[
            VerificationLabel.LEAN_VERIFIED,
            VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED,
        ],
        evidence_boundary_instructions=["Generated prose is not evidence."],
        style_instructions=["Use placeholder-grade prose."],
        max_words=120,
        source_contract_hashes={"claim_table": "0" * 64},
    )


def _claim_table(label: VerificationLabel = VerificationLabel.CONJECTURE) -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[
            Claim(
                claim_id="claim-main",
                claim_text="The example remains conjectural.",
                claim_label=label,
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
