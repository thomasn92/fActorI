"""Deterministic prompt contracts for gated Stage B LLM reviewers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from factori.schemas import Candidate, ReviewerPromptContract

REVIEWER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviews"],
    "properties": {
        "reviews": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "reviewer_id",
                    "candidate_id",
                    "novelty_score",
                    "feasibility_score",
                    "verifiability_score",
                    "clarity_score",
                    "significance_score",
                    "objections",
                    "recommendation",
                ],
                "properties": {
                    "reviewer_id": {"type": "string"},
                    "candidate_id": {"type": "string"},
                    "novelty_score": {"type": "number"},
                    "feasibility_score": {"type": "number"},
                    "verifiability_score": {"type": "number"},
                    "clarity_score": {"type": "number"},
                    "significance_score": {"type": "number"},
                    "objections": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "recommendation": {
                        "type": "string",
                        "enum": [
                            "Accept",
                            "WeakAccept",
                            "Revise",
                            "WeakReject",
                            "Reject",
                        ],
                    },
                },
            },
        }
    },
}


def build_stage_b_reviewer_prompt(
    candidate: Candidate,
    rubric: Mapping[str, Any],
    retrieval_context: Mapping[str, Any] | None,
    max_objections: int,
) -> ReviewerPromptContract:
    """Build a stable reviewer-only prompt with explicit authority boundaries."""
    if max_objections < 1:
        raise ValueError("max_objections must be at least 1")
    candidate_summary = {
        "candidate_id": candidate.id,
        "question": candidate.question,
        "hypothesis": candidate.hypothesis,
        "theory": candidate.theory,
        "experiment": candidate.experiment,
        "baseline": candidate.baseline,
        "assumptions": candidate.symbolic_state.get("assumptions", []),
        "variant_type": candidate.variant_type,
    }
    forbidden_outputs = [
        "LeanVerified",
        "ExperimentVerified",
        "SyntheticExperimentVerified",
        "RealDataExperimentVerified",
        "claims of proof verification",
        "claims of experiment verification",
        "claims of real-world validation",
        "publication approval",
        "claims of exhaustive literature coverage",
    ]
    evidence_instructions = [
        "Produce structural reviewer critique only.",
        "The review is not proof, experiment, retrieval, human approval, or scientific validation.",
        "Retrieval context is bounded context and is not exhaustive literature coverage.",
        "Do not assign or imply any verification label.",
        "Return JSON compatible with the requested schema and no additional fields.",
    ]
    retrieval_summary = dict(retrieval_context) if retrieval_context is not None else None
    prompt_payload = {
        "task": "Stage B structural reviewer panel",
        "candidate": candidate_summary,
        "domain": candidate.domain or "unspecified",
        "method": candidate.method,
        "data_requirement": candidate.data_requirement.value,
        "retrieval_context": retrieval_summary,
        "rubric": dict(rubric),
        "panel_roles": ["novelty", "methods", "skeptic"],
        "max_objections_per_review": max_objections,
        "forbidden_outputs": forbidden_outputs,
        "evidence_boundary_instructions": evidence_instructions,
        "requested_output_schema": REVIEWER_OUTPUT_SCHEMA,
    }
    return ReviewerPromptContract(
        candidate_id=candidate.id,
        candidate_summary=candidate_summary,
        domain=candidate.domain or "unspecified",
        method=candidate.method,
        data_requirement=candidate.data_requirement,
        retrieval_context_summary=retrieval_summary,
        rubric=dict(rubric),
        requested_output_schema=REVIEWER_OUTPUT_SCHEMA,
        forbidden_outputs=forbidden_outputs,
        evidence_boundary_instructions=evidence_instructions,
        max_objections=max_objections,
        prompt_text=json.dumps(prompt_payload, sort_keys=True, ensure_ascii=True),
    )


__all__ = ["REVIEWER_OUTPUT_SCHEMA", "build_stage_b_reviewer_prompt"]
