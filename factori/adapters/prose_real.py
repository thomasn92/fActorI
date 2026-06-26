"""Gated OpenAI prose adapter for one manuscript section."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import (
    LLMTransport,
    OpenAIResponsesTransport,
    _extract_output_text,
)
from factori.adapters.prose_prompts import build_prose_section_prompt
from factori.adapters.prose_safety import parse_prose_generation_response
from factori.schemas import (
    ClaimTable,
    GeneratedSectionDraft,
    ManuscriptSectionPlan,
    NarrativeManuscriptContract,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSectionContract,
)


@dataclass
class OpenAIProseGenerator:
    """Real-but-gated prose generator restricted to section drafting."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    allow_external_calls: bool = False
    backend_name: str = field(default="openai", init=False)
    provider_name: str = field(default="openai", init=False)
    is_fake: bool = field(default=False, init=False)
    generation_requests: list[ProseGenerationRequest] = field(default_factory=list, init=False)
    parse_results: list[ProseGenerationParseResult] = field(default_factory=list, init=False)
    raw_responses: list[Any] = field(default_factory=list, init=False)

    @property
    def external_calls_enabled(self) -> bool:
        """Expose the explicit gate through the shared adapter metadata protocol."""
        return self.allow_external_calls

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "prose adapters."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "Real prose adapter requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("Real prose adapter requires a non-empty model name.")

    def generate_section(
        self,
        section_contract: ManuscriptSectionPlan | ProseSectionContract | dict[str, Any],
        claim_table: ClaimTable,
    ) -> GeneratedSectionDraft:
        """Protocol-compatible entry point for tests and future callers."""
        if isinstance(section_contract, ProseSectionContract):
            prose_contract = section_contract
        elif isinstance(section_contract, ManuscriptSectionPlan):
            prose_contract = ProseSectionContract(
                run_id="unknown",
                section_id=section_contract.section_id,
                section_title=section_contract.title,
                section_role=section_contract.title,
                narrative_role=section_contract.narrative_roles,
                allowed_claim_ids=section_contract.allowed_claim_ids,
                allowed_evidence_artifact_ids=sorted(
                    {
                        evidence_id
                        for claim in claim_table.claims
                        if claim.claim_id in set(section_contract.allowed_claim_ids)
                        for evidence_id in claim.evidence_artifact_ids
                    }
                ),
                forbidden_labels=[],
            )
        else:
            prose_contract = ProseSectionContract.model_validate(section_contract)
        narrative = NarrativeManuscriptContract(
            contract_id="prose-adapter-direct-call",
            run_id=prose_contract.run_id,
        )
        prompt_contract = build_prose_section_prompt(
            prose_contract,
            claim_table,
            _evidence_map_from_claim_table(claim_table),
            narrative,
            backend=self.backend_name,
            provider=self.provider_name,
        )
        parse_result = self.generate_section_from_prompt(prompt_contract)
        if parse_result.section_draft is None:
            raise AdapterResponseParseError(
                backend=self.backend_name,
                provider=self.provider_name,
                operation="generate_section",
                message="prose adapter did not return a valid section draft",
            )
        return parse_result.section_draft

    def generate_section_from_prompt(
        self,
        prompt_contract: ProsePromptContract,
    ) -> ProseGenerationParseResult:
        """Request structured prose and parse it locally."""
        request = ProseGenerationRequest(
            run_id=prompt_contract.run_id,
            section_id=prompt_contract.section_id,
            prompt_contract=prompt_contract,
            backend=self.backend_name,
            provider=self.provider_name,
            model=self.model,
            allow_external_calls=True,
            fake=False,
        )
        raw_response = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt_contract.prompt_text,
            response_schema=prompt_contract.requested_output_schema,
        )
        sanitized = _json_compatible(raw_response)
        if isinstance(sanitized, dict) and "output" in sanitized:
            parsed_input: Any = _extract_output_text(sanitized)
        else:
            parsed_input = sanitized
        parse_result = parse_prose_generation_response(parsed_input)
        self.generation_requests.append(request)
        self.raw_responses.append(sanitized)
        self.parse_results.append(parse_result)
        return parse_result


def _json_compatible(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            message="prose transport returned a non-JSON-compatible response",
            cause=exc,
        ) from exc


def _evidence_map_from_claim_table(claim_table: ClaimTable) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for claim in claim_table.claims:
        for evidence_id in claim.evidence_artifact_ids:
            evidence[evidence_id] = {
                "claim_id": claim.claim_id,
                "claim_label": claim.claim_label.value,
                "artifact_id": evidence_id,
            }
    return evidence


__all__ = ["OpenAIProseGenerator"]
