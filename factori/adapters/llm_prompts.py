"""Deterministic prompt contracts for gated Stage A LLM candidate proposals."""

from __future__ import annotations

from factori.hashing import canonical_json
from factori.schemas import ConstraintSet, DataRequirement, LLMPromptContract

STAGE_A_CANDIDATE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "domain",
                    "method",
                    "claim_type",
                    "assumptions",
                    "data_requirement",
                    "possible_synthetic_experiment",
                    "risks",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "domain": {"type": "string"},
                    "method": {"type": ["string", "null"]},
                    "claim_type": {"type": "string"},
                    "question": {"type": ["string", "null"]},
                    "hypothesis": {"type": ["string", "null"]},
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "primitives": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data_requirement": {
                        "type": "string",
                        "enum": [item.value for item in DataRequirement],
                    },
                    "possible_synthetic_experiment": {"type": ["string", "null"]},
                    "baseline": {"type": ["string", "null"]},
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


def build_stage_a_candidate_prompt(
    domain: str,
    method: str | None,
    constraints: ConstraintSet,
    max_candidates: int,
) -> LLMPromptContract:
    """Build a stable structured-output contract for candidate ideas only."""
    normalized_domain = " ".join(domain.split())
    if not normalized_domain:
        raise ValueError("domain is required for Stage A candidate generation")
    normalized_method = " ".join(method.split()) if method else None
    data_policy = list(DataRequirement)
    mvp_gate = {
        "allowed": [DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY],
        "deferred": [DataRequirement.PUBLIC_DOWNLOAD, DataRequirement.USER_PROVIDED],
    }
    forbidden_claims = [
        "Do not claim LeanVerified, ExperimentVerified, SyntheticExperimentVerified, or "
        "RealDataExperimentVerified.",
        "Do not claim proof, completed experiments, literature evidence, or human approval.",
        "Do not describe synthetic evidence as real-world validation.",
        "Do not invent evidence or citations.",
    ]
    evidence_instructions = [
        "Generate candidate research ideas only.",
        "Prefer NoData or SyntheticOnly under the current MVP data gate.",
        "PublicDownload and UserProvided proposals will be deferred by Stage A.",
        "Return JSON compatible with the requested schema and include data_requirement.",
        "Candidate proposals are context artifacts and are not verification evidence.",
    ]
    prompt_payload = {
        "task": "Propose research candidates for deterministic Stage A validation.",
        "domain": normalized_domain,
        "method": normalized_method,
        "constraints": constraints.model_dump(mode="json"),
        "max_candidates": max_candidates,
        "mvp_data_gate": {
            key: [item.value for item in values] for key, values in mvp_gate.items()
        },
        "required_candidate_fields": [
            "title",
            "domain",
            "method",
            "claim_type",
            "assumptions",
            "data_requirement",
            "possible_synthetic_experiment",
            "risks",
        ],
        "forbidden_claims": forbidden_claims,
        "evidence_boundary_instructions": evidence_instructions,
    }
    prompt_text = (
        "Follow this Stage A candidate-generation contract exactly. "
        "Return structured JSON only.\n" + canonical_json(prompt_payload)
    )
    return LLMPromptContract(
        domain=normalized_domain,
        method=normalized_method,
        constraints=constraints.model_dump(mode="json"),
        data_regime_policy=data_policy,
        mvp_data_gate=mvp_gate,
        requested_output_schema=STAGE_A_CANDIDATE_RESPONSE_SCHEMA,
        forbidden_claims=forbidden_claims,
        evidence_boundary_instructions=evidence_instructions,
        max_candidates=max_candidates,
        prompt_text=prompt_text,
    )


__all__ = ["STAGE_A_CANDIDATE_RESPONSE_SCHEMA", "build_stage_a_candidate_prompt"]
