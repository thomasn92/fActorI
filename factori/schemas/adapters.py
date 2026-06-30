"""Adapter request, response, trace, and safety schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    DataRequirement,
    LLMBudgetDecisionStatus,
    LLMCallStatus,
    LLMOrchestrationStatus,
    LLMOrchestrationStepStatus,
    NarrativeSectionRole,
    VerificationLabel,
)
from factori.schemas.stages import StageBReviewerReport


class GeneratedSectionDraft(StrictModel):
    """Deterministic placeholder from a prose adapter, never polished prose."""

    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_artifact_ids: list[str] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    unsupported_sentences: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    polished: bool = False
    fake: bool = True
    is_verification_evidence: bool = False


class ProseSectionContract(StrictModel):
    """Section-level prose contract; not a scientific evidence artifact."""

    run_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    section_role: str = Field(min_length=1)
    narrative_role: list[NarrativeSectionRole] = Field(default_factory=list)
    allowed_claim_ids: list[str] = Field(default_factory=list)
    allowed_evidence_artifact_ids: list[str] = Field(default_factory=list)
    allowed_citation_ids: list[str] = Field(default_factory=list)
    allowed_citation_keys: list[str] = Field(default_factory=list)
    citation_policy: Literal["none", "registry-only"] = "none"
    allowed_statement_classes: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    forbidden_labels: list[VerificationLabel] = Field(default_factory=list)
    evidence_boundary_instructions: list[str] = Field(default_factory=list)
    citation_boundary_instructions: list[str] = Field(default_factory=list)
    literature_positioning_context: dict[str, Any] | None = None
    style_instructions: list[str] = Field(default_factory=list)
    max_words: int = Field(default=160, ge=1, le=5000)
    required_subsections: list[str] = Field(default_factory=list)
    source_contract_hashes: dict[str, str] = Field(default_factory=dict)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class ProsePromptContract(StrictModel):
    """Deterministic prompt contract for a single manuscript section."""

    run_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    backend: str = "fake"
    provider: str = "fake"
    section_contract: ProseSectionContract
    allowed_claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    narrative_context: dict[str, Any] = Field(default_factory=dict)
    requested_output_schema: dict[str, Any]
    forbidden_outputs: list[str]
    evidence_boundary_instructions: list[str]
    prompt_text: str = Field(min_length=1)
    fake: bool = True
    is_verification_evidence: bool = False


class ProseGenerationRequest(StrictModel):
    """Provider-neutral request sent to a gated prose adapter."""

    run_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    prompt_contract: ProsePromptContract
    backend: str = "fake"
    provider: str = "fake"
    model: str | None = None
    allow_external_calls: bool = False
    fake: bool = True
    is_verification_evidence: bool = False


class ProseGenerationParseResult(StrictModel):
    """Parsed single-section prose generation response."""

    section_draft: GeneratedSectionDraft | None = None
    rejected: bool = False
    reasons: list[str] = Field(default_factory=list)
    raw_response_type: str = Field(min_length=1)
    fake: bool = False
    is_verification_evidence: bool = False


class ProseSafetyReport(StrictModel):
    """Safety report for generated prose; diagnostic only."""

    section_id: str = Field(min_length=1)
    safe: bool
    rejected: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_artifact_ids: list[str] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    allowed_statement_classes_used: list[str] = Field(default_factory=list)
    safe_scaffold_sentences_retained: list[str] = Field(default_factory=list)
    unsafe_sentences_removed: list[str] = Field(default_factory=list)
    sanitized_content: str | None = None
    original_sentence_count: int = Field(default=0, ge=0)
    removed_sentence_count: int = Field(default=0, ge=0)
    retained_sentence_count: int = Field(default=0, ge=0)
    section_status: str = "retained"
    removal_reasons: list[str] = Field(default_factory=list)
    forbidden_labels_detected: list[str] = Field(default_factory=list)
    forbidden_labels_allowed_as_scaffold: list[str] = Field(default_factory=list)
    created_or_upgraded_labels: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class HumanReviewDecision(StrictModel):
    """Adapter response that explicitly records no real human review in the MVP."""

    request_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    approved: bool = False
    reviewer_is_human: bool = False
    reason: str = Field(min_length=1)
    fake: bool = True


class ReviewerPromptContract(StrictModel):
    """Deterministic prompt contract for Stage B structural critique only."""

    candidate_id: str = Field(min_length=1)
    candidate_summary: dict[str, Any]
    domain: str = Field(min_length=1)
    method: str | None = None
    data_requirement: DataRequirement
    retrieval_context_summary: dict[str, Any] | None = None
    rubric: dict[str, Any]
    requested_output_schema: dict[str, Any]
    forbidden_outputs: list[str]
    evidence_boundary_instructions: list[str]
    max_objections: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)


class ReviewerValidationResult(StrictModel):
    """Safety result for one LLM-generated structural reviewer report."""

    reviewer_id: str | None = None
    candidate_id: str | None = None
    valid: bool
    reasons: list[str] = Field(default_factory=list)


class LLMReviewerParseResult(StrictModel):
    """Normalized non-evidence Stage B reviewer response."""

    reports: list[StageBReviewerReport] = Field(default_factory=list)
    rejected_reports: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    fallback_used: bool = False
    reasons: list[str] = Field(default_factory=list)
    fake: bool = False
    is_verification_evidence: bool = False


class LLMReviewerTrace(StrictModel):
    """Sanitized LLM reviewer request/response retained as non-evidence context."""

    request: dict[str, Any]
    raw_response: Any
    parse_result: LLMReviewerParseResult
    fake: bool = False
    is_verification_evidence: bool = False


class LLMPromptContract(StrictModel):
    """Deterministic Stage A prompt contract for candidate proposal only."""

    domain: str = Field(min_length=1)
    method: str | None = None
    constraints: dict[str, Any]
    data_regime_policy: list[DataRequirement]
    mvp_data_gate: dict[str, list[DataRequirement]]
    requested_output_schema: dict[str, Any]
    forbidden_claims: list[str]
    evidence_boundary_instructions: list[str]
    max_candidates: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)


class CandidateValidationResult(StrictModel):
    """Safety result for one LLM-proposed candidate."""

    candidate_id: str | None = None
    valid: bool
    deferred_by_mvp_data_gate: bool = False
    reasons: list[str] = Field(default_factory=list)


class LLMCandidateParseReport(StrictModel):
    """Non-evidence parse summary for one structured LLM response."""

    accepted_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    max_candidates: int = Field(ge=1)
    truncated: bool = False
    fake: bool = False
    is_verification_evidence: bool = False


class LLMGenerationTrace(StrictModel):
    """Sanitized request/response trace retained as non-evidence provenance context."""

    request: dict[str, Any]
    raw_response: Any
    parse_report: LLMCandidateParseReport
    fake: bool = False
    is_verification_evidence: bool = False


class LLMBudgetConfig(StrictModel):
    """Explicit budget limits for gated end-to-end LLM orchestration."""

    max_total_calls: int | None = Field(default=None, ge=0)
    max_candidate_generation_calls: int | None = Field(default=None, ge=0)
    max_review_calls: int | None = Field(default=None, ge=0)
    max_prose_calls: int | None = Field(default=None, ge=0)
    max_claim_adjudication_calls: int | None = Field(default=None, ge=0)
    max_source_relevance_adjudication_calls: int | None = Field(default=None, ge=0)
    max_quality_repair_calls: int | None = Field(default=None, ge=0)
    max_total_input_tokens: int | None = Field(default=None, ge=0)
    max_total_output_tokens: int | None = Field(default=None, ge=0)
    max_estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    max_wallclock_seconds: int | None = Field(default=None, ge=1)
    max_retries_per_call: int = Field(default=0, ge=0, le=10)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    fail_on_budget_unknown: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LLMBudgetUsage(StrictModel):
    """Deterministic planned or observed LLM usage accounting."""

    total_calls: int = Field(default=0, ge=0)
    candidate_generation_calls: int = Field(default=0, ge=0)
    review_calls: int = Field(default=0, ge=0)
    prose_calls: int = Field(default=0, ge=0)
    claim_adjudication_calls: int = Field(default=0, ge=0)
    source_relevance_adjudication_calls: int = Field(default=0, ge=0)
    quality_repair_calls: int = Field(default=0, ge=0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    unknown_token_usage: bool = False
    unknown_cost: bool = False
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LLMBudgetDecision(StrictModel):
    """Preflight budget decision for a gated LLM orchestration request."""

    decision_status: LLMBudgetDecisionStatus
    allowed: bool
    budget_config: LLMBudgetConfig
    planned_usage: LLMBudgetUsage
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LLMCallAccountingRecord(StrictModel):
    """Secret-safe accounting record for one planned or observed LLM call."""

    step_name: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str | None = None
    request_hash: str = Field(min_length=64, max_length=64)
    response_hash: str | None = Field(default=None, min_length=64, max_length=64)
    input_token_estimate: int | None = Field(default=None, ge=0)
    output_token_estimate: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    status: LLMCallStatus
    error_type: str | None = None
    retry_status: str = "retry_not_enabled"
    external_call_performed: bool = False
    contains_secret: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LLMRunSafetyReport(StrictModel):
    """Evidence-boundary report for gated LLM orchestration outputs."""

    run_id: str = Field(min_length=1)
    safe: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_outputs_are_verification_evidence: bool = False
    llm_reviews_are_proof_evidence: bool = False
    llm_prose_is_proof_evidence: bool = False
    llm_prose_is_experiment_evidence: bool = False
    llm_prose_is_retrieval_evidence: bool = False
    release_status_is_publication_readiness: bool = False
    created_or_upgraded_labels: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LLMOrchestrationConfig(StrictModel):
    """Configuration for explicit end-to-end LLM-assisted paper orchestration."""

    run_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    method: str | None = None
    candidate_backend: str = "fake"
    reviewer_backend: str = "fake"
    prose_backend: str = "fake"
    allow_external_calls: bool = False
    llm_model: str = "gpt-5-mini"
    reviewer_model: str = "gpt-5-mini"
    prose_model: str = "gpt-5-mini"
    claim_adjudicator_backend: Literal["off", "fake", "openai"] = "off"
    claim_adjudicator_model: str = "gpt-5-mini"
    source_relevance_adjudicator_backend: Literal["off", "fake", "openai"] = "off"
    source_relevance_adjudicator_model: str = "gpt-5-mini"
    quality_repair_backend: Literal["off", "deterministic", "fake", "openai"] = "off"
    quality_repair_model: str = "gpt-5-mini"
    reviewer_max_objections: int = Field(default=5, ge=1, le=20)
    generate_paper: bool = True
    evaluate_release: bool = True
    include_citations: bool = True
    enable_retrieval: bool = False
    retrieval_backend: str = "fake"
    retrieval_local_path: str | None = None
    max_retrieval_sources: int = Field(default=5, ge=1, le=100)
    citation_policy: Literal["none", "registry-only"] = "none"
    export_latex: bool = True
    critique: bool = True
    revise: bool = False
    apply_safe_fake_revision: bool = False
    reexport_latex_after_revision: bool = False
    render_check: bool = False
    allow_external_tools: bool = False
    latex_executable: str | None = None
    write_report: bool = False
    rerun_policy: str = "fail-if-exists"
    force: bool = False
    budget: LLMBudgetConfig = Field(default_factory=LLMBudgetConfig)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LLMOrchestrationStep(StrictModel):
    """One step in the explicit LLM-assisted paper workflow."""

    step_name: str = Field(min_length=1)
    status: LLMOrchestrationStepStatus
    summary: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LLMOrchestrationReport(StrictModel):
    """Report for gated end-to-end LLM-assisted paper orchestration."""

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    config: LLMOrchestrationConfig
    orchestration_status: LLMOrchestrationStatus
    steps: list[LLMOrchestrationStep] = Field(default_factory=list)
    budget_decision: LLMBudgetDecision
    budget_usage: LLMBudgetUsage
    call_accounting: list[LLMCallAccountingRecord] = Field(default_factory=list)
    safety_report: LLMRunSafetyReport
    selected_backends: dict[str, str] = Field(default_factory=dict)
    generate_paper_status: str | None = None
    release_status: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    publication_ready: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LLMOrchestrationResult(StrictModel):
    """Typed CLI/protocol result for gated LLM paper orchestration."""

    run_id: str = Field(min_length=1)
    orchestration_status: LLMOrchestrationStatus
    report: LLMOrchestrationReport
    full_paper_generation_status: str | None = None
    paper_release_status: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


__all__ = [
    "GeneratedSectionDraft",
    "HumanReviewDecision",
    "LLMBudgetConfig",
    "LLMBudgetDecision",
    "LLMBudgetUsage",
    "LLMCallAccountingRecord",
    "ReviewerPromptContract",
    "ReviewerValidationResult",
    "LLMRunSafetyReport",
    "LLMOrchestrationConfig",
    "LLMOrchestrationReport",
    "LLMOrchestrationResult",
    "LLMOrchestrationStep",
    "LLMReviewerParseResult",
    "LLMReviewerTrace",
    "ProseGenerationParseResult",
    "ProseGenerationRequest",
    "ProsePromptContract",
    "ProseSafetyReport",
    "ProseSectionContract",
    "LLMPromptContract",
    "CandidateValidationResult",
    "LLMCandidateParseReport",
    "LLMGenerationTrace",
]
