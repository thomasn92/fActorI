"""Generic targeted-study orchestration contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from factori.schemas.adapters import LLMBudgetUsage
from factori.schemas.base import HASH_RE, StrictModel
from factori.schemas.enums import BackendKind
from factori.schemas.manuscript import StageBackendRecord

TargetedStudyMode = Literal["preflight", "smoke", "full"]
TargetedStudyStatus = Literal[
    "preflight_ready",
    "completed",
    "completed_with_warnings",
    "deferred",
    "failed",
]
TargetedStudyStageStatus = Literal["planned", "completed", "reused", "deferred", "failed"]
AdaptiveQuestionStatus = Literal["pass", "fail", "unknown", "not_applicable"]
AdaptiveEvidenceAction = Literal[
    "accept_supported_result",
    "accept_negative_result",
    "repair_code",
    "repair_evidence_plan",
    "downgrade_claim",
    "stop_weak_branch",
    "stop_no_progress",
    "stop_budget_exhausted",
    "blocked",
]
AdaptiveEvidenceLoopStatus = Literal[
    "in_progress",
    "satisfied_supported",
    "satisfied_negative",
    "stopped_weak_branch",
    "stopped_no_progress",
    "budget_exhausted",
    "blocked",
]
AdaptiveClaimDisposition = Literal[
    "supported",
    "negative_result",
    "inconclusive",
    "deferred",
    "rejected",
]


class AdaptiveEvidenceLoopConfig(StrictModel):
    """Bounded controls for post-M103 question, repair, and stopping behavior."""

    max_questioner_iterations: int = Field(default=3, ge=1, le=64)
    max_code_repair_calls: int = Field(default=2, ge=0, le=32)
    max_plan_repair_calls: int = Field(default=1, ge=0, le=8)
    no_progress_limit: int = Field(default=1, ge=1, le=8)


class AdaptiveQuestionerAnswer(StrictModel):
    """One artifact-grounded answer to a context-selected scientific question."""

    question_id: str = Field(min_length=1)
    category: Literal[
        "implementation_fidelity",
        "numerical_validity",
        "baseline_control_adequacy",
        "evidence_sufficiency",
        "claim_scope",
        "repair_sufficiency",
        "stopping",
    ]
    question: str = Field(min_length=1)
    status: AdaptiveQuestionStatus
    explanation: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    blocking: bool
    recommended_fix_optional: str | None = None
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    is_verification_evidence: Literal[False] = False


class AdaptiveEvidenceDecision(StrictModel):
    """One normalized questioner decision subject to deterministic policy validation."""

    decision_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    iteration: int = Field(ge=1)
    source_package_report_id: str = Field(min_length=1)
    source_execution_report_id: str = Field(min_length=1)
    source_package_ids: list[str] = Field(min_length=1)
    source_result_ids: list[str] = Field(default_factory=list)
    source_code_artifact_ids: list[str] = Field(default_factory=list)
    questions: list[AdaptiveQuestionerAnswer] = Field(min_length=1)
    deterministic_findings: list[str] = Field(default_factory=list)
    action: AdaptiveEvidenceAction
    rationale: str = Field(min_length=1)
    repair_instructions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    claim_disposition: AdaptiveClaimDisposition
    diagnostic_fingerprint: str = Field(pattern=HASH_RE.pattern)
    backend_kind: BackendKind
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False
    creates_verified_theorem: Literal[False] = False
    novelty_proven: Literal[False] = False
    is_verification_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_repair_instructions(self) -> AdaptiveEvidenceDecision:
        if self.action in {"repair_code", "repair_evidence_plan"} and not self.repair_instructions:
            raise ValueError("repair decisions require explicit repair instructions")
        return self


class AdaptiveEvidenceIteration(StrictModel):
    """One append-only adaptive question-and-repair iteration."""

    iteration_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    decision: AdaptiveEvidenceDecision
    source_package_report_path: str = Field(min_length=1)
    source_execution_report_path: str = Field(min_length=1)
    produced_package_report_path_optional: str | None = None
    produced_execution_report_path_optional: str | None = None
    before_fingerprint: str = Field(pattern=HASH_RE.pattern)
    after_fingerprint_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    progress_made: bool
    external_calls_used: int = Field(default=1, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    is_verification_evidence: Literal[False] = False


class AdaptiveQuestionerRawArtifact(StrictModel):
    """Secret-free raw adaptive-questioner call provenance."""

    raw_artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    iteration: int = Field(ge=1)
    backend_name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_text: str = Field(min_length=1)
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted_decision_id_optional: str | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    is_verification_evidence: Literal[False] = False


class AdaptiveEvidenceLoopReport(StrictModel):
    """Terminal or resumable report for the bounded adaptive evidence loop."""

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    config: AdaptiveEvidenceLoopConfig
    status: AdaptiveEvidenceLoopStatus
    iterations: list[AdaptiveEvidenceIteration] = Field(default_factory=list)
    accepted_result_ids: list[str] = Field(default_factory=list)
    code_repair_attempt_count: int = Field(default=0, ge=0)
    code_repair_success_count: int = Field(default=0, ge=0)
    plan_repair_attempt_count: int = Field(default=0, ge=0)
    plan_repair_success_count: int = Field(default=0, ge=0)
    unresolved_blocking_questions: list[str] = Field(default_factory=list)
    unresolved_obligations: list[str] = Field(default_factory=list)
    terminal_reason: str = Field(min_length=1)
    latest_package_report_path: str = Field(min_length=1)
    latest_execution_report_path: str = Field(min_length=1)
    call_accounting_paths: list[str] = Field(default_factory=list)
    budget_usage: LLMBudgetUsage = Field(default_factory=LLMBudgetUsage)
    backend_records: list[StageBackendRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    production_ready: bool = False
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False
    creates_verified_theorem: Literal[False] = False
    novelty_proven: Literal[False] = False
    is_verification_evidence: Literal[False] = False


class TargetedResearchBrief(StrictModel):
    """User-selected scientific direction supplied to the autonomous LLM stages."""

    brief_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    method: str = Field(min_length=1)
    central_question: str = Field(min_length=1)
    hypothesis_optional: str | None = None
    theory_or_model_object_optional: str | None = None
    experiment_or_proof_direction_optional: str | None = None
    baseline_candidates: list[str] = Field(default_factory=list)
    expected_metrics: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    negative_controls: list[str] = Field(default_factory=list)
    data_regime: str = Field(min_length=1)
    known_risks: list[str] = Field(default_factory=list)
    allowed_claim_scope: str = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    source_run_id_optional: str | None = None
    source_candidate_ids: list[str] = Field(default_factory=list)
    source_artifact_paths: list[str] = Field(default_factory=list)
    source_content_hashes: dict[str, str] = Field(default_factory=dict)
    authoring_backend_kind: BackendKind = BackendKind.HUMAN
    selection_backend_kind: BackendKind = BackendKind.HUMAN
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    implies_publication_readiness: Literal[False] = False
    is_verification_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_source_hashes(self) -> TargetedResearchBrief:
        for name, digest in self.source_content_hashes.items():
            if not name or not HASH_RE.fullmatch(digest):
                raise ValueError("source content hashes require a non-empty key and SHA-256 value")
        return self


class TargetedStudyConfig(StrictModel):
    """Fail-closed execution configuration for one targeted study."""

    run_id: str = Field(min_length=1)
    mode: TargetedStudyMode = "preflight"
    brief_path_optional: str | None = None
    source_run_id_optional: str | None = None
    candidate_id_optional: str | None = None
    backend: str = "llm-openai"
    model: str = Field(min_length=1)
    reasoning_effort: Literal["default", "low", "medium", "high"] = "default"
    llm_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    retrieval_mode: Literal["mocked_retrieval", "real_retrieval"] = "real_retrieval"
    allow_external_calls: bool = False
    require_non_fake_backends: bool = True
    resume: bool = False
    max_total_calls: int = Field(ge=0)
    max_cost_usd: float = Field(ge=0.0)
    estimated_input_tokens_per_call: int = Field(default=12_000, ge=1)
    estimated_output_tokens_per_call: int = Field(default=4_000, ge=1)
    input_cost_per_million_usd: float = Field(default=1.0, ge=0.0)
    output_cost_per_million_usd: float = Field(default=6.0, ge=0.0)
    experiment_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    experiment_memory_limit_mb: int = Field(default=1024, ge=64, le=4096)
    max_replications: int = Field(default=100, ge=1, le=10_000)
    max_resamples: int = Field(default=200, ge=1, le=20_000)
    max_grid_cells: int = Field(default=12, ge=1, le=1024)
    render_final_pdf: bool = False
    latex_executable: str | None = None
    latex_timeout_seconds: int = Field(default=300, ge=1, le=600)
    adaptive_evidence: AdaptiveEvidenceLoopConfig = Field(
        default_factory=AdaptiveEvidenceLoopConfig
    )

    @model_validator(mode="after")
    def validate_execution_gate(self) -> TargetedStudyConfig:
        if self.mode != "preflight":
            if not self.allow_external_calls:
                raise ValueError("smoke/full targeted studies require allow_external_calls=true")
            if not self.require_non_fake_backends:
                raise ValueError("smoke/full targeted studies require strict non-fake mode")
            if self.max_total_calls < 1 or self.max_cost_usd <= 0:
                raise ValueError(
                    "smoke/full targeted studies require positive call and cost budgets"
                )
        if bool(self.brief_path_optional) == bool(self.candidate_id_optional):
            raise ValueError("provide exactly one of brief_path_optional or candidate_id_optional")
        if self.candidate_id_optional and not self.source_run_id_optional:
            raise ValueError("candidate source requires source_run_id_optional")
        if self.render_final_pdf:
            if self.mode != "full":
                raise ValueError("final PDF rendering is available only in full mode")
            if not self.latex_executable:
                raise ValueError(
                    "render_final_pdf=true requires an explicit latex_executable"
                )
        return self


class TargetedStudyStageRecord(StrictModel):
    """One planned, completed, reused, or blocked targeted-study stage."""

    stage_name: str = Field(min_length=1)
    status: TargetedStudyStageStatus
    artifact_paths: list[str] = Field(default_factory=list)
    external_call_budget: int = Field(default=0, ge=0)
    reused: bool = False
    warnings: list[str] = Field(default_factory=list)
    error_optional: str | None = None


class TargetedStudyCheckpoint(StrictModel):
    """Append-only stage-level resume checkpoint."""

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    config_hash: str = Field(pattern=HASH_RE.pattern)
    brief_hash: str = Field(pattern=HASH_RE.pattern)
    completed_stage_names: list[str] = Field(default_factory=list)
    latest_stage_optional: str | None = None
    stage_records: list[TargetedStudyStageRecord] = Field(default_factory=list)
    call_accounting_paths: list[str] = Field(default_factory=list)
    publication_ready: Literal[False] = False


class TargetedStudyRunReport(StrictModel):
    """Top-level report for preflight, smoke, or full targeted orchestration."""

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: TargetedStudyStatus
    mode: TargetedStudyMode
    brief: TargetedResearchBrief
    config: TargetedStudyConfig
    planned_external_call_count: int = Field(ge=0)
    minimum_required_external_call_count: int = Field(default=0, ge=0)
    completed_external_call_count_upper_bound: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)
    minimum_estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    budget_usage: LLMBudgetUsage = Field(default_factory=LLMBudgetUsage)
    call_accounting_paths: list[str] = Field(default_factory=list)
    adaptive_evidence_report_path_optional: str | None = None
    adaptive_evidence_status_optional: AdaptiveEvidenceLoopStatus | None = None
    stage_records: list[TargetedStudyStageRecord] = Field(default_factory=list)
    checkpoint_paths: list[str] = Field(default_factory=list)
    terminal_artifact_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    production_ready: bool = False
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    implies_publication_readiness: Literal[False] = False
    is_verification_evidence: Literal[False] = False


class TargetedStudyInspectionReport(StrictModel):
    """Read-only view of the latest targeted-study report."""

    run_id: str = Field(min_length=1)
    targeted_study_present: bool
    latest_report_optional: TargetedStudyRunReport | None = None
    checkpoint_count: int = Field(default=0, ge=0)
    completed_stage_names: list[str] = Field(default_factory=list)
    next_stage_optional: str | None = None
    adaptive_evidence_status_optional: AdaptiveEvidenceLoopStatus | None = None
    adaptive_iteration_count: int = Field(default=0, ge=0)
    adaptive_last_action_optional: AdaptiveEvidenceAction | None = None
    adaptive_code_repair_count: int = Field(default=0, ge=0)
    adaptive_plan_repair_count: int = Field(default=0, ge=0)
    adaptive_unresolved_blocking_questions: list[str] = Field(default_factory=list)
    budget_usage: LLMBudgetUsage = Field(default_factory=LLMBudgetUsage)
    warnings: list[str] = Field(default_factory=list)
    production_ready: bool = False
    publication_ready: Literal[False] = False


__all__ = [
    "AdaptiveEvidenceDecision",
    "AdaptiveEvidenceIteration",
    "AdaptiveEvidenceLoopConfig",
    "AdaptiveEvidenceLoopReport",
    "AdaptiveQuestionerAnswer",
    "AdaptiveQuestionerRawArtifact",
    "TargetedResearchBrief",
    "TargetedStudyCheckpoint",
    "TargetedStudyConfig",
    "TargetedStudyInspectionReport",
    "TargetedStudyRunReport",
    "TargetedStudyStageRecord",
]
