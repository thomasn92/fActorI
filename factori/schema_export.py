"""Deterministic JSON Schema and example export for fActorI protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from factori.adapters.config import AdapterConfig
from factori.protocols import (
    PROTOCOL_GENERATOR,
    PROTOCOL_SOURCE,
    PROTOCOL_VERSION,
    SCHEMA_FORMAT,
    ProtocolDefinition,
    get_protocol_definitions,
)
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    BibliographyEntry,
    Candidate,
    CitationRecord,
    CitationRegistry,
    CitationSafetyReport,
    CitationUsage,
    Claim,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    CompleteMarkdownDraft,
    ConstraintSet,
    DataRequirement,
    DryRunStatus,
    ExperimentKind,
    ExperimentRunContract,
    ExperimentRunResult,
    FullPaperArtifactBundle,
    FullPaperBundleCompletenessReport,
    FullPaperEvidenceBoundaryReport,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStatus,
    FullPaperGenerationStep,
    FullPaperGenerationStepStatus,
    FullPaperReadinessDecision,
    FullPaperReleaseCheck,
    FullPaperReleaseFindingSeverity,
    FullPaperReleaseGateConfig,
    FullPaperReleaseReport,
    FullPaperReleaseStatus,
    GeneratedSectionDraft,
    KernelLedgerVerifyRequest,
    KernelMode,
    KernelOperation,
    KernelRequestEnvelope,
    KernelResponseEnvelope,
    KernelResponseStatus,
    LatexCompileCheckReport,
    LatexExportContract,
    LatexExportResult,
    LatexRenderConfig,
    LatexRenderResult,
    LatexSafetyReport,
    LatexSourceMap,
    LatexSourceMapEntry,
    LedgerTipStatus,
    LedgerTipValidationReport,
    LiteratureGapStatement,
    LiteraturePositioningContract,
    LiteraturePositioningReport,
    LLMBudgetConfig,
    LLMBudgetDecision,
    LLMBudgetDecisionStatus,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCallStatus,
    LLMCandidateParseReport,
    LLMOrchestrationConfig,
    LLMOrchestrationReport,
    LLMOrchestrationResult,
    LLMOrchestrationStatus,
    LLMOrchestrationStep,
    LLMOrchestrationStepStatus,
    LLMPromptContract,
    LLMRunSafetyReport,
    ManuscriptAssemblyReport,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    ManuscriptDraftStatus,
    NextStageRecommendation,
    PaperCriticFinding,
    PaperCriticFindingSeverity,
    PaperCriticFindingType,
    PaperCriticReport,
    PaperReleaseReadinessPreview,
    PaperRevisionActionKind,
    PaperRevisionPatch,
    PaperRevisionPlan,
    PaperRevisionResult,
    PaperRevisionStatus,
    PipelineDryRunPlan,
    PipelineFailurePolicy,
    PipelineRunReport,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
    PlannedOutput,
    PlannedStage,
    PlannedStageStatus,
    ProofVerificationContract,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    ResearchObjectManifest,
    ResumeValidationReport,
    ResumeValidationStatus,
    RetrievalQuery,
    RetrievalResult,
    RevisionSafetyReport,
    RunCompletenessStatus,
    RunStatusReport,
    SectionDraftingResult,
    SectionDraftingTask,
    SectionDraftSafetySummary,
    SectionRevisionPlan,
    SourceProvenance,
    StageCheckpoint,
    StagePrerequisite,
    TargetedResearchBrief,
    VerificationLabel,
)

DEFAULT_PROTOCOL_OUTPUT_DIR = Path("protocols/jsonschema")
_HASH = "0" * 64
_TIMESTAMP_PROPERTY_NAMES = frozenset(
    {
        "created_at",
        "finished_at",
        "retrieved_at",
        "started_at",
        "timestamp",
    }
)

EXAMPLE_PROTOCOLS: dict[str, str] = {
    "adapter-config.example.json": "AdapterConfig",
    "artifact.example.json": "ArtifactRecord",
    "artifact-manifest.example.json": "ArtifactManifest",
    "candidate.example.json": "Candidate",
    "citation-registry.example.json": "CitationRegistry",
    "citation-safety-report.example.json": "CitationSafetyReport",
    "claim-support-audit.example.json": "ClaimSupportAuditReport",
    "claim.example.json": "Claim",
    "experiment-result.example.json": "ExperimentRunResult",
    "experiment-contract.example.json": "ExperimentRunContract",
    "ledger-tip-validation-report.example.json": "LedgerTipValidationReport",
    "llm-orchestration-result.example.json": "LLMOrchestrationResult",
    "llm-candidate-request.example.json": "LLMPromptContract",
    "llm-candidate-response.example.json": "LLMCandidateParseReport",
    "pipeline-dry-run-plan.example.json": "PipelineDryRunPlan",
    "pipeline-run-report.example.json": "PipelineRunReport",
    "prose-generation-parse-result.example.json": "ProseGenerationParseResult",
    "prose-generation-request.example.json": "ProseGenerationRequest",
    "prose-prompt-contract.example.json": "ProsePromptContract",
    "prose-safety-report.example.json": "ProseSafetyReport",
    "prose-section-contract.example.json": "ProseSectionContract",
    "literature-positioning-report.example.json": "LiteraturePositioningReport",
    "manuscript-drafting-plan.example.json": "ManuscriptDraftingPlan",
    "section-drafting-result.example.json": "SectionDraftingResult",
    "complete-markdown-draft.example.json": "CompleteMarkdownDraft",
    "manuscript-drafting-report.example.json": "ManuscriptDraftingReport",
    "manuscript-assembly-report.example.json": "ManuscriptAssemblyReport",
    "latex-source-map.example.json": "LatexSourceMap",
    "latex-render-result.example.json": "LatexRenderResult",
    "latex-export-result.example.json": "LatexExportResult",
    "paper-critic-report.example.json": "PaperCriticReport",
    "paper-revision-result.example.json": "PaperRevisionResult",
    "full-paper-generation-result.example.json": "FullPaperGenerationResult",
    "full-paper-golden-bundle.example.json": "FullPaperArtifactBundle",
    "full-paper-release-report.example.json": "FullPaperReleaseReport",
    "proof-contract.example.json": "ProofVerificationContract",
    "proof-result.example.json": "ProofVerificationResult",
    "research-object-manifest.example.json": "ResearchObjectManifest",
    "resume-validation-report.example.json": "ResumeValidationReport",
    "retrieval-query.example.json": "RetrievalQuery",
    "retrieval-result.example.json": "RetrievalResult",
    "run-status-report.example.json": "RunStatusReport",
    "stage-result.example.json": "StageResult",
    "targeted-research-brief.example.json": "TargetedResearchBrief",
    "kernel-request-envelope.example.json": "KernelRequestEnvelope",
    "kernel-ledger-request-envelope.example.json": "KernelRequestEnvelope",
    "kernel-response-envelope.example.json": "KernelResponseEnvelope",
}


class ProtocolExportError(RuntimeError):
    """Raised when checked-in protocol files differ from generated contracts."""


@dataclass(frozen=True)
class ProtocolExportResult:
    """Summary of a deterministic protocol export or check."""

    output_dir: Path
    schema_files: tuple[Path, ...]
    example_files: tuple[Path, ...]
    version_file: Path
    stale_files: tuple[Path, ...] = ()

    @property
    def up_to_date(self) -> bool:
        return not self.stale_files


def export_protocols(output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR) -> ProtocolExportResult:
    """Write deterministic schemas, metadata, and small validated examples."""
    expected = _expected_files(output_dir)
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    expected_schemas = {path for path in expected if path.parent == output_dir}
    if output_dir.is_dir():
        for existing in output_dir.glob("*.schema.json"):
            if existing not in expected_schemas:
                existing.unlink()
    return _result(output_dir)


def check_protocols(output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR) -> ProtocolExportResult:
    """Compare generated contracts with disk without writing any file."""
    expected = _expected_files(output_dir)
    stale = [
        path
        for path, content in expected.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]
    expected_schemas = {path for path in expected if path.parent == output_dir}
    if output_dir.is_dir():
        stale.extend(
            path
            for path in output_dir.glob("*.schema.json")
            if path not in expected_schemas
        )
    return _result(output_dir, stale_files=tuple(sorted(set(stale))))


def require_protocols_current(
    output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR,
) -> ProtocolExportResult:
    """Return a successful check or raise with deterministic stale paths."""
    result = check_protocols(output_dir)
    if not result.up_to_date:
        stale = ", ".join(path.as_posix() for path in result.stale_files)
        raise ProtocolExportError(f"Protocol files are stale or missing: {stale}")
    return result


def build_protocol_schema(definition: ProtocolDefinition) -> dict[str, Any]:
    """Generate one language-neutral schema from its existing typed model."""
    schema = TypeAdapter(definition.model).json_schema(mode="serialization")
    _normalize_protocol_schema(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        f"https://schemas.factori.local/{PROTOCOL_VERSION}/{definition.filename}"
    )
    schema["title"] = definition.name
    schema["description"] = definition.description
    schema["x-factori-protocol-version"] = PROTOCOL_VERSION
    schema["x-factori-source-model"] = definition.source_model
    schema["x-factori-verification-evidence"] = False
    return schema


def protocol_metadata() -> dict[str, str]:
    """Return stable metadata for consumers in any implementation language."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_format": SCHEMA_FORMAT,
        "source": PROTOCOL_SOURCE,
        "generated_by": PROTOCOL_GENERATOR,
    }


def protocol_examples() -> dict[str, dict[str, Any]]:
    """Return small deterministic examples validated by their source models."""
    candidate = Candidate(
        id="candidate-example",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        domain="machine learning",
        method="calibration",
        question="Can calibration remain stable under a declared synthetic shift?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    artifact = ArtifactRef(
        id="artifact-example",
        type=ArtifactType.REPORT,
        path="runs/example/reports/artifact-example.json",
        content_hash=_HASH,
        producing_commit_hash=_HASH,
        metadata={"is_verification_evidence": False},
    )
    artifact_manifest = ArtifactManifest(
        run_id="example",
        artifacts=[
            ArtifactManifestEntry(
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                path=artifact.path,
                content_hash=artifact.content_hash,
                producing_commit_hash=artifact.producing_commit_hash,
                is_evidence=False,
                is_presentation=True,
                metadata={"example": True},
            )
        ],
        evidence_artifact_count=0,
        presentation_artifact_count=1,
    )
    stage_result = PipelineStageResult(
        stage_name=PipelineStage.RUN_STAGE_A,
        started_at="1970-01-01T00:00:00.000000Z",
        finished_at="1970-01-01T00:00:01.000000Z",
        status=PipelineRunStatus.PIPELINE_SUCCEEDED,
        created_artifacts=[artifact.path],
        summary={"stage_a_survivors": 1},
    )
    provenance = SourceProvenance(
        source_id="source-example",
        provider="fake",
        query="calibration synthetic shift",
        rank=0,
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash=_HASH,
    )
    retrieval = RetrievalResult(
        source_id="source-example",
        title="Synthetic calibration context",
        authors=["Example Author"],
        year=1970,
        provider="fake",
        retrieved_at="1970-01-01T00:00:00.000000Z",
        query="calibration synthetic shift",
        rank=0,
        raw_metadata_hash=_HASH,
        source_provenance=provenance,
        fake=True,
    )
    citation_record = CitationRecord(
        citation_id="citation-source-example",
        citation_key="Author1970SyntheticCalibrationContext",
        source_id=retrieval.source_id,
        title=retrieval.title,
        authors=retrieval.authors,
        year=retrieval.year,
        venue=retrieval.venue,
        doi=retrieval.doi,
        url=retrieval.url,
        provider=retrieval.provider,
        retrieved_at=retrieval.retrieved_at,
        raw_metadata_hash=retrieval.raw_metadata_hash,
        source_artifact_id="retrieval-normalized-results-example",
    )
    bibliography_entry = BibliographyEntry(
        citation_id=citation_record.citation_id,
        citation_key=citation_record.citation_key,
        source_id=citation_record.source_id,
        markdown=(
            "- [@Author1970SyntheticCalibrationContext] Example Author (1970). "
            "Synthetic calibration context. Source: `source-example`."
        ),
        has_source_provenance=True,
    )
    citation_registry = CitationRegistry(
        run_id="example",
        citations=[citation_record],
        bibliography=[bibliography_entry],
        citation_key_policy=(
            "FirstAuthorYearShortTitle; duplicate keys receive deterministic letters."
        ),
        source_registry_hash=_HASH,
    )
    citation_safety_report = CitationSafetyReport(
        run_id="example",
        safe=True,
        rejected=False,
        citation_usages=[
            CitationUsage(
                citation_key=citation_record.citation_key,
                count=1,
                known=True,
                citation_id=citation_record.citation_id,
            )
        ],
        used_citation_keys=[citation_record.citation_key],
        used_citation_ids=[citation_record.citation_id],
        bibliography_entries_count=1,
    )
    claim_support_supported = ClaimSupportItem(
        sentence_id="introduction-p0-s0",
        section_name="Introduction",
        sentence_text_hash=_HASH,
        sentence_snippet=(
            "Retrieved fixture metadata provides bounded background context "
            "[@Author1970SyntheticCalibrationContext]."
        ),
        claim_class="source_context_claim",
        citation_keys_present=[citation_record.citation_key],
        required_support_type="registry_background_context",
        supporting_source_ids=[citation_record.source_id],
        support_status="registry_supported",
    )
    claim_support_scaffold = ClaimSupportItem(
        sentence_id="limitations-p0-s0",
        section_name="Limitations",
        sentence_text_hash=_HASH,
        sentence_snippet="This draft is manuscript context only and does not create evidence.",
        claim_class="evidence_boundary_statement",
        citation_keys_present=[],
        required_support_type="none",
        supporting_source_ids=[],
        support_status="not_required_scaffold",
    )
    claim_support_audit = ClaimSupportAuditReport(
        run_id="example",
        citation_registry_present=True,
        citation_policy="registry-only",
        claim_support_items=[claim_support_supported, claim_support_scaffold],
        summary_counts={
            "total_sentences": 2,
            "registry_supported": 1,
            "scaffold_not_required": 1,
            "missing_required_citation": 0,
            "scope_mismatch": 0,
            "forbidden_claim": 0,
            "citation_as_validation_misuse": 0,
        },
        unsupported_items=[],
    )
    literature_contract = LiteraturePositioningContract(
        run_id="example",
        contract_id="literature-positioning-example",
        problem_context="Bounded calibration context.",
        retrieval_queries_used=["calibration synthetic shift"],
        included_citation_ids=[citation_record.citation_id],
        literature_gap_statement="The example frames a bounded gap without claiming coverage.",
        novelty_positioning_statement=(
            "Retrieval metadata may frame context but cannot prove novelty."
        ),
        coverage_limitations=[
            "Retrieval is bounded context, not exhaustive literature coverage.",
        ],
        non_exhaustiveness_disclaimer=(
            "Retrieval is bounded context, not exhaustive literature coverage; "
            "retrieval adequacy is not proof of novelty."
        ),
    )
    literature_gap = LiteratureGapStatement(
        statement_id="literature-gap-example",
        problem_context=literature_contract.problem_context,
        statement=literature_contract.literature_gap_statement,
        citation_ids=[citation_record.citation_id],
        citation_keys=[citation_record.citation_key],
        limitations=literature_contract.coverage_limitations,
    )
    literature_positioning_report = LiteraturePositioningReport(
        run_id="example",
        citation_registry_id="citation-registry-example",
        contract=literature_contract,
        gap_statement=literature_gap,
        markdown_intro_paragraph=(
            "This draft uses bounded retrieval context "
            "[@Author1970SyntheticCalibrationContext]. Retrieval is not novelty proof."
        ),
        literature_limitations_paragraph=(
            "Literature positioning is bounded context, not exhaustive coverage."
        ),
        citation_keys_used=[citation_record.citation_key],
    )
    retrieval_query = RetrievalQuery(
        query_id="query-example",
        query="calibration synthetic shift",
        provider="fake",
        limit=2,
        endpoint="fake://retrieval",
        parameters={"query": "calibration synthetic shift"},
        requires_credentials=False,
        fake=True,
    )
    experiment_contract = ExperimentRunContract(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        experiment_id="experiment-example",
        experiment_kind=ExperimentKind.SYNTHETIC_SIMULATION,
        data_regime=DataRequirement.SYNTHETIC_ONLY,
        synthetic_data_spec={"samples": 16, "external_data": False},
        model_spec={"claim": "synthetic calibration behavior"},
        algorithm_spec={"runner": "deterministic-example"},
        metrics=["delta", "lcb_95"],
        acceptance_criteria={"delta": {"min": 0.1}, "lcb_95": {"min": 0.0}},
        random_seed=0,
        replications=3,
        backend="fake",
        runner_name="fake",
    )
    experiment = ExperimentRunResult(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        experiment_id="experiment-example",
        backend="local_synthetic",
        provider="local",
        experiment_kind=ExperimentKind.SYNTHETIC_SIMULATION,
        data_regime=DataRequirement.SYNTHETIC_ONLY,
        runner_name="local_synthetic",
        exit_code=1,
        stdout_hash=_HASH,
        stderr_hash=_HASH,
        input_spec_hash=_HASH,
        output_payload_hash=_HASH,
        metrics={"delta": 0.05, "lcb_95": -0.01},
        acceptance_criteria={"delta": {"min": 0.1}, "lcb_95": {"min": 0.0}},
        passed=False,
        label=VerificationLabel.NEGATIVE_RESULT,
        reason="The deterministic example does not meet its declared threshold.",
        fake=False,
    )
    proof_contract = ProofVerificationContract(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        claim_text="The example proof claim remains conjectural.",
        proof_language="Lean",
        backend="fake",
        proof_payload_text="theorem factori_example : True := by\n  trivial\n",
        proof_payload={"attempt": "deterministic-placeholder"},
        allow_external_calls=False,
        allow_external_tools=False,
    )
    proof_result = ProofVerificationResult(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        backend="lean",
        provider="lean",
        proof_language="Lean",
        tool_name="lean",
        exit_code=1,
        stdout_hash=_HASH,
        stderr_hash=_HASH,
        proof_payload_hash=_HASH,
        forbidden_tokens_present=False,
        verified=False,
        label=VerificationLabel.CONJECTURE,
        reason="Deterministic example only; no external proof tool was executed.",
        fake=False,
    )
    claim = Claim(
        claim_id="claim-example",
        claim_text="The proposed relationship remains conjectural.",
        claim_label=VerificationLabel.CONJECTURE,
        candidate_id=candidate.id,
        allowed_in_main_text=False,
        allowed_section="Theory",
        reason="No proof evidence is linked.",
    )
    prose_section_contract = ProseSectionContract(
        run_id="example",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=[claim.claim_id],
        allowed_evidence_artifact_ids=[artifact.id],
        allowed_citation_ids=[citation_record.citation_id],
        allowed_citation_keys=[citation_record.citation_key],
        forbidden_claims=[],
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
        evidence_boundary_instructions=[
            "Use only allowed claim IDs.",
            "Generated prose is not verification evidence.",
        ],
        citation_boundary_instructions=[
            "Use only allowed citation keys.",
            "Do not claim retrieval proves novelty.",
        ],
        literature_positioning_context=literature_positioning_report.model_dump(
            mode="json"
        ),
        style_instructions=["Use placeholder-grade prose only."],
        max_words=120,
        source_contract_hashes={"claim_table": _HASH},
    )
    prose_prompt_contract = ProsePromptContract(
        run_id="example",
        section_id=prose_section_contract.section_id,
        section_contract=prose_section_contract,
        allowed_claims=[claim.model_dump(mode="json")],
        evidence_map={
            artifact.id: {
                "artifact_id": artifact.id,
                "claim_id": claim.claim_id,
                "is_verification_evidence": False,
            }
        },
        narrative_context={"central_message": "Bounded example only."},
        requested_output_schema={"type": "object"},
        forbidden_outputs=["Do not invent citations or verification labels."],
        evidence_boundary_instructions=[
            "Draft prose only.",
            "Do not upgrade claim labels.",
        ],
        prompt_text="Draft the introduction using only claim-example and artifact-example.",
    )
    prose_request = ProseGenerationRequest(
        run_id="example",
        section_id=prose_section_contract.section_id,
        prompt_contract=prose_prompt_contract,
    )
    generated_section = GeneratedSectionDraft(
        section_id=prose_section_contract.section_id,
        title=prose_section_contract.section_title,
        content=(
            "[FAKE PROSE DRAFT] This section summarizes claim-example using "
            "artifact-example and [@Author1970SyntheticCalibrationContext]. "
            "No scientific label is upgraded."
        ),
        claim_ids=[claim.claim_id],
        used_claim_ids=[claim.claim_id],
        used_evidence_artifact_ids=[artifact.id],
        used_citation_ids=[citation_record.citation_id],
        used_citation_keys=[citation_record.citation_key],
    )
    prose_parse_result = ProseGenerationParseResult(
        section_draft=generated_section,
        raw_response_type="dict",
        fake=True,
    )
    prose_safety_report = ProseSafetyReport(
        section_id=prose_section_contract.section_id,
        safe=True,
        rejected=False,
        used_claim_ids=[claim.claim_id],
        used_evidence_artifact_ids=[artifact.id],
        used_citation_ids=[citation_record.citation_id],
        used_citation_keys=[citation_record.citation_key],
    )
    section_task = SectionDraftingTask(
        section_id=prose_section_contract.section_id,
        section_title=prose_section_contract.section_title,
        section_role=prose_section_contract.section_role,
        narrative_role=prose_section_contract.narrative_role,
        allowed_claim_ids=prose_section_contract.allowed_claim_ids,
        allowed_evidence_artifact_ids=prose_section_contract.allowed_evidence_artifact_ids,
        allowed_citation_ids=prose_section_contract.allowed_citation_ids,
        allowed_citation_keys=prose_section_contract.allowed_citation_keys,
        source_contract_hashes=prose_section_contract.source_contract_hashes,
        prose_contract=prose_section_contract,
    )
    manuscript_drafting_plan = ManuscriptDraftingPlan(
        run_id="example",
        plan_id="manuscript-drafting-plan-example",
        manuscript_plan_id="manuscript-plan-example",
        narrative_contract_id="narrative-contract-example",
        paper_shape_critique_id="paper-shape-critique-example",
        sections_count=1,
        tasks=[section_task],
        warnings=["Fake prose draft is a deterministic placeholder."],
    )
    section_drafting_result = SectionDraftingResult(
        section_id=generated_section.section_id,
        section_title=generated_section.title,
        section_role=prose_section_contract.section_role,
        narrative_role=prose_section_contract.narrative_role,
        draft_markdown=generated_section.content,
        used_claim_ids=generated_section.used_claim_ids,
        used_evidence_artifact_ids=generated_section.used_evidence_artifact_ids,
        used_citation_ids=generated_section.used_citation_ids,
        used_citation_keys=generated_section.used_citation_keys,
        safety_status="Safe",
        warnings=generated_section.warnings,
        unsupported_sentences=generated_section.unsupported_sentences,
        source_contract_hashes=prose_section_contract.source_contract_hashes,
        safe=True,
        rejected=False,
        safety_reasons=[],
        draft=generated_section,
        safety_report=prose_safety_report,
    )
    complete_markdown_draft = CompleteMarkdownDraft(
        run_id="example",
        title="Deterministic example manuscript draft",
        markdown=(
            "# Deterministic example manuscript draft\n\n"
            "## Introduction\n\n"
            "[FAKE PROSE DRAFT] This section summarizes claim-example.\n\n"
            "## Claim/Evidence Appendix\n\n"
            "- `claim-example`: artifact-example\n\n"
            "## Provenance Appendix\n\n"
            "- Draft artifacts are presentation/context only.\n"
        ),
        section_ids=[generated_section.section_id],
        claim_evidence_appendix="- `claim-example`: artifact-example",
        provenance_appendix="- Draft artifacts are presentation/context only.",
        warnings=["Fake prose draft is a deterministic placeholder."],
    )
    manuscript_assembly_report = ManuscriptAssemblyReport(
        run_id="example",
        assembled_sections=1,
        omitted_sections=[],
        unsafe_section_ids=[],
        warnings=["Fake prose draft is a deterministic placeholder."],
        draft_status=ManuscriptDraftStatus.DRAFT_COMPLETE_WITH_WARNINGS,
        complete_markdown_artifact_id="complete-manuscript-draft",
    )
    manuscript_drafting_report = ManuscriptDraftingReport(
        run_id="example",
        drafting_plan_id=manuscript_drafting_plan.plan_id,
        sections_total=1,
        sections_safe=1,
        sections_unsafe=0,
        draft_status=ManuscriptDraftStatus.DRAFT_COMPLETE_WITH_WARNINGS,
        section_summaries=[
            SectionDraftSafetySummary(
                section_id=generated_section.section_id,
                safety_status="Safe",
                safe=True,
                rejected=False,
                warnings=generated_section.warnings,
                used_claim_ids=generated_section.used_claim_ids,
                used_evidence_artifact_ids=generated_section.used_evidence_artifact_ids,
                used_citation_ids=generated_section.used_citation_ids,
                used_citation_keys=generated_section.used_citation_keys,
            )
        ],
        warnings=["Fake prose draft is a deterministic placeholder."],
        manuscript_draft_artifact_id="complete-manuscript-draft",
        assembly_report_artifact_id="manuscript-assembly-report",
    )
    latex_contract = LatexExportContract(
        run_id="example",
        manuscript_draft_artifact_id="complete-manuscript-draft",
        citation_registry_artifact_id="citation-registry",
        section_order=["Introduction"],
        source_map_policy=(
            "Each generated LaTeX section maps back to manuscript sections, "
            "claims, evidence artifacts, and citation keys."
        ),
        allowed_citation_keys=[citation_record.citation_key],
        allowed_claim_ids=[claim.claim_id],
        allowed_evidence_artifact_ids=[artifact.id],
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
    )
    latex_source_entry = LatexSourceMapEntry(
        latex_block_id="latex-block-001",
        section_id="introduction",
        section_title="Introduction",
        claim_ids=[claim.claim_id],
        evidence_artifact_ids=[artifact.id],
        citation_keys=[citation_record.citation_key],
        markdown_line_range=[1, 4],
        latex_line_range=[7, 10],
        source_contract_hashes={"claim_table": _HASH},
    )
    latex_source_map = LatexSourceMap(
        run_id="example",
        entries=[latex_source_entry],
        source_map_policy=latex_contract.source_map_policy,
        covers_all_major_sections=True,
    )
    latex_safety_report = LatexSafetyReport(
        run_id="example",
        safe=True,
        rejected=False,
        used_citation_keys=[citation_record.citation_key],
        source_map_sections=["introduction"],
    )
    latex_render_config = LatexRenderConfig(
        run_id="example",
        render_check_enabled=False,
        allow_external_tools=False,
        latex_executable=None,
    )
    latex_render_result = LatexRenderResult(
        run_id="example",
        backend="local_latex",
        tool_name="not_configured",
        exit_code=0,
        stdout_hash=_HASH,
        stderr_hash=_HASH,
        tex_hash=_HASH,
        passed=True,
        warnings=["LaTeX render check was not requested."],
        reason="Render check skipped.",
    )
    latex_compile_check = LatexCompileCheckReport(
        run_id="example",
        config=latex_render_config,
        render_result=latex_render_result,
        passed=True,
        warnings=latex_render_result.warnings,
    )
    latex_export_result = LatexExportResult(
        run_id="example",
        contract=latex_contract,
        paper_tex=(
            "\\documentclass{article}\n"
            "\\usepackage{hyperref}\n"
            "\\title{Deterministic example manuscript draft}\n"
            "\\begin{document}\n"
            "\\maketitle\n"
            "\\section{Introduction}\n"
            "This draft cites bounded context \\cite{Author1970SyntheticCalibrationContext}.\n"
            "\\bibliography{references}\n"
            "\\end{document}\n"
        ),
        references_bib=(
            "@misc{Author1970SyntheticCalibrationContext,\n"
            "  title = {Synthetic calibration context}\n"
            "}\n"
        ),
        source_map=latex_source_map,
        safety_report=latex_safety_report,
        render_result=latex_render_result,
        compile_check_report=latex_compile_check,
        warnings=["LaTeX export is presentation/context only."],
    )
    paper_critic_finding = PaperCriticFinding(
        finding_id="paper-finding-001",
        finding_type=PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
        severity=PaperCriticFindingSeverity.WARNING,
        section_id="introduction",
        section_title="Introduction",
        message="bounded literature disclaimer is missing",
        recommended_action=PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP,
        source="markdown",
    )
    paper_readiness_preview = PaperReleaseReadinessPreview(
        run_id="example",
        ready_for_revision_review=True,
        blocking_findings=0,
        major_findings=0,
        warning_findings=1,
        publication_ready=False,
        reasons=["Paper critic preview is not publication readiness."],
    )
    paper_critic_report = PaperCriticReport(
        report_id="paper-critic-report-example",
        run_id="example",
        manuscript_draft_artifact_id="complete-manuscript-draft",
        latex_artifact_id="paper",
        source_map_artifact_id="latex-source-map",
        findings=[paper_critic_finding],
        findings_count=1,
        blocking_findings=0,
        major_findings=0,
        warning_findings=1,
        info_findings=0,
        citation_safe=True,
        latex_safe=True,
        source_map_covered=True,
        release_readiness_preview=paper_readiness_preview,
    )
    section_revision_plan = SectionRevisionPlan(
        section_id="introduction",
        section_title="Introduction",
        actions=[PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP],
        finding_ids=[paper_critic_finding.finding_id],
        notes=[paper_critic_finding.message],
    )
    paper_revision_plan = PaperRevisionPlan(
        plan_id="paper-revision-plan-example",
        run_id="example",
        critic_report_id=paper_critic_report.report_id,
        actions=[PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP],
        section_plans=[section_revision_plan],
        safe_to_apply=True,
        warnings=["Revision plan is not publication readiness."],
    )
    paper_revision_patch = PaperRevisionPatch(
        patch_id="paper-revision-patch-001",
        action=PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP,
        target_section_id="introduction",
        before_snippet="## Introduction",
        after_snippet=(
            "## Introduction\n\nLiterature positioning is bounded and non-exhaustive."
        ),
        rationale="inserted bounded literature disclaimer",
    )
    revision_safety_report = RevisionSafetyReport(
        run_id="example",
        safe=True,
        rejected=False,
        warnings=[
            "Paper revision artifacts are manuscript/revision context only and cannot "
            "create evidence."
        ],
        known_citation_keys_preserved=[citation_record.citation_key],
    )
    paper_revision_result = PaperRevisionResult(
        run_id="example",
        revision_status=PaperRevisionStatus.REVISION_APPLIED_WITH_WARNINGS,
        critic_report_id=paper_critic_report.report_id,
        revision_plan_id=paper_revision_plan.plan_id,
        revised_markdown=(
            "# Deterministic example manuscript draft\n\n"
            "## Introduction\n\n"
            "Literature positioning is bounded and non-exhaustive.\n"
        ),
        patches=[paper_revision_patch],
        safety_report=revision_safety_report,
    )
    full_paper_config = FullPaperGenerationConfig(
        run_id="example",
        include_citations=True,
        export_latex=True,
        critique=True,
        revise=True,
        apply_safe_fake_revision=True,
        reexport_latex_after_revision=True,
        prose_backend="fake",
        write_report=True,
    )
    full_paper_step = FullPaperGenerationStep(
        step_name="draft-manuscript",
        status=FullPaperGenerationStepStatus.SUCCEEDED_WITH_WARNINGS,
        summary="Manuscript draft artifacts were generated.",
        artifact_ids=["complete-manuscript-draft"],
        warnings=["Generated paper artifacts are not evidence."],
    )
    full_paper_bundle = FullPaperArtifactBundle(
        run_id="example",
        citation_registry_artifact_id="citation-registry",
        literature_positioning_report_artifact_id="literature-positioning-report",
        citation_safety_report_artifact_id="citation-safety-report",
        claim_support_audit_artifact_id="claim-support-audit",
        manuscript_drafting_plan_artifact_id="manuscript-drafting-plan",
        manuscript_drafting_report_artifact_id="manuscript-drafting-report",
        complete_manuscript_draft_artifact_id="complete-manuscript-draft",
        manuscript_assembly_report_artifact_id="manuscript-assembly-report",
        latex_artifact_id="paper",
        references_artifact_id="references",
        latex_source_map_artifact_id="latex-source-map",
        latex_export_report_artifact_id="latex-export-report",
        latex_safety_report_artifact_id="latex-safety-report",
        paper_critic_report_artifact_id="paper-critic-report",
        paper_revision_plan_artifact_id="paper-revision-plan",
        revision_safety_report_artifact_id="revision-safety-report",
        revised_manuscript_draft_artifact_id="revised-manuscript-draft",
        paper_revision_result_artifact_id="paper-revision-result",
        revised_latex_artifact_id="revised-paper",
        full_paper_generation_report_artifact_id="full-paper-generation-report",
        full_paper_artifact_bundle_artifact_id="full-paper-artifact-bundle",
        artifact_ids=[
            "citation-registry",
            "claim-support-audit",
            "complete-manuscript-draft",
            "paper",
            "paper-critic-report",
            "revised-manuscript-draft",
            "full-paper-generation-report",
        ],
    )
    full_paper_report = FullPaperGenerationReport(
        report_id="full-paper-generation-report-example",
        run_id="example",
        config=full_paper_config,
        generation_status=(
            FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS
        ),
        steps=[full_paper_step],
        artifact_bundle=full_paper_bundle,
        warnings=["Full-paper generation is not publication readiness."],
        revision_applied=True,
        render_check_requested=False,
    )
    full_paper_result = FullPaperGenerationResult(
        run_id="example",
        generation_status=full_paper_report.generation_status,
        report=full_paper_report,
        artifact_bundle=full_paper_bundle,
    )
    full_paper_golden_bundle = full_paper_bundle.model_copy(
        update={
            "run_id": "golden-paper",
            "revised_references_artifact_id": "revised-references",
            "revised_latex_source_map_artifact_id": "revised-latex-source-map",
            "revised_latex_export_report_artifact_id": "revised-latex-export-report",
            "revised_latex_safety_report_artifact_id": "revised-latex-safety-report",
            "artifact_ids": [
                "citation-registry",
                "citation-safety-report",
                "claim-support-audit",
                "complete-manuscript-draft",
                "full-paper-artifact-bundle",
                "full-paper-generation-report",
                "latex-export-report",
                "latex-safety-report",
                "latex-source-map",
                "literature-positioning-report",
                "manuscript-assembly-report",
                "manuscript-drafting-plan",
                "manuscript-drafting-report",
                "paper",
                "paper-critic-report",
                "paper-revision-plan",
                "paper-revision-result",
                "references",
                "revised-latex-export-report",
                "revised-latex-safety-report",
                "revised-latex-source-map",
                "revised-manuscript-draft",
                "revised-paper",
                "revised-references",
                "revision-safety-report",
            ],
        }
    )
    full_paper_release_config = FullPaperReleaseGateConfig(run_id="example")
    full_paper_completeness = FullPaperBundleCompletenessReport(
        run_id="example",
        required_artifact_ids=["complete-manuscript-draft", "paper"],
        present_artifact_ids=["complete-manuscript-draft", "paper"],
        complete=True,
    )
    full_paper_evidence_boundary = FullPaperEvidenceBoundaryReport(
        run_id="example",
        safe=True,
        warnings=["Human-review readiness is not publication readiness."],
        claim_table_unchanged=True,
        evidence_classification_unchanged=True,
    )
    full_paper_release_decision = FullPaperReadinessDecision(
        run_id="example",
        status=FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
        ready_for_human_review=True,
        warnings=["Human review remains required."],
    )
    full_paper_release_report = FullPaperReleaseReport(
        report_id="full-paper-release-report-example",
        run_id="example",
        config=full_paper_release_config,
        checks=[
            FullPaperReleaseCheck(
                check_id="required_artifacts",
                passed=True,
                severity=FullPaperReleaseFindingSeverity.BLOCKING,
                message="Required generated-paper artifacts are present.",
                artifact_ids=["complete-manuscript-draft", "paper"],
            )
        ],
        completeness=full_paper_completeness,
        evidence_boundary=full_paper_evidence_boundary,
        decision=full_paper_release_decision,
        revision_status="NotApplied",
    )
    pipeline_report = PipelineRunReport(
        run_id="example",
        domain="machine learning",
        stage_results=[stage_result],
        started_at="1970-01-01T00:00:00.000000Z",
        finished_at="1970-01-01T00:00:01.000000Z",
        pipeline_status=PipelineRunStatus.PIPELINE_SUCCEEDED,
        failure_policy=PipelineFailurePolicy.CONTINUE_SAFE,
        pipeline_report_path="runs/example/reports/pipeline-run-report.md",
    )
    checkpoint = StageCheckpoint(
        stage_name=PipelineStage.RUN_STAGE_A,
        completed=True,
        required_artifacts_present=["runs/example/reports/stage-a-report.md"],
        completion_evidence=["stage_a_report"],
    )
    next_stage = NextStageRecommendation(
        stage_name=PipelineStage.RUN_STAGE_B,
        command="uv run factori run-stage-b --run-id example",
        reason="Stage A example checkpoint is complete.",
    )
    run_status = RunStatusReport(
        run_id="example",
        run_exists=True,
        completed_stages=[PipelineStage.RUN_STAGE_A],
        missing_stages=[PipelineStage.RUN_STAGE_B],
        next_recommended_stage=next_stage,
        last_completed_stage=PipelineStage.RUN_STAGE_A,
        required_artifacts_present=["runs/example/reports/stage-a-report.md"],
        stage_checkpoints=[checkpoint],
        ledger_exists=True,
        ledger_commit_count=3,
        artifact_manifest_exists=False,
        research_object_exists=False,
        paper_skeleton_exists=False,
        final_audit_exists=False,
        export_preparation_exists=False,
        replay_report_exists=False,
        diagnostic_report_exists=False,
        completeness_status=RunCompletenessStatus.PARTIAL_RUN,
    )
    prerequisite = StagePrerequisite(
        stage_name=PipelineStage.RUN_STAGE_B,
        required_prior_stage=PipelineStage.RUN_STAGE_A,
        required_artifact_path_or_kind="reports/stage-a-report.md",
        required_report="stage-a-report",
        message="Stage B requires Stage A survivor output.",
    )
    resume_validation = ResumeValidationReport(
        run_id="example",
        start_at_stage=PipelineStage.RUN_STAGE_B,
        resume_status=ResumeValidationStatus.RESUME_ALLOWED,
        prerequisites=[prerequisite],
        next_recommended_stage=next_stage,
        run_exists=True,
        ledger_exists=True,
        ledger_commit_count=3,
    )
    ledger_tip = LedgerTipValidationReport(
        run_id="example",
        status=LedgerTipStatus.VALID,
        commit_count=3,
        tip_hashes=[_HASH],
        ledger_exists=True,
    )
    llm_prompt = LLMPromptContract(
        domain="machine learning",
        method="calibration",
        constraints=ConstraintSet(
            domain="machine learning",
            method="calibration",
            data_requirement=DataRequirement.SYNTHETIC_ONLY,
        ).model_dump(mode="json"),
        data_regime_policy=list(DataRequirement),
        mvp_data_gate={
            "allowed": [DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY],
            "deferred": [
                DataRequirement.PUBLIC_DOWNLOAD,
                DataRequirement.USER_PROVIDED,
            ],
        },
        requested_output_schema={
            "type": "object",
            "properties": {"candidates": {"type": "array"}},
        },
        forbidden_claims=["Do not claim verification labels."],
        evidence_boundary_instructions=["Candidate proposals are not evidence."],
        max_candidates=2,
        prompt_text="Deterministic example prompt contract.",
    )
    llm_response = LLMCandidateParseReport(
        accepted_candidate_ids=[candidate.id],
        rejected_candidates=[],
        max_candidates=2,
        truncated=False,
        fake=True,
    )
    llm_budget = LLMBudgetConfig(
        max_total_calls=10,
        max_estimated_cost_usd=1.0,
        rate_limit_per_minute=6,
        fail_on_budget_unknown=False,
    )
    llm_usage = LLMBudgetUsage(
        total_calls=0,
        candidate_generation_calls=0,
        review_calls=0,
        prose_calls=0,
        unknown_token_usage=False,
        unknown_cost=False,
        rate_limit_per_minute=6,
    )
    llm_budget_decision = LLMBudgetDecision(
        decision_status=LLMBudgetDecisionStatus.ALLOWED_WITH_WARNINGS,
        allowed=True,
        budget_config=llm_budget,
        planned_usage=llm_usage,
        warnings=["Fake orchestration performs no external LLM calls."],
        rate_limit_per_minute=6,
    )
    llm_call_record = LLMCallAccountingRecord(
        step_name="llm-candidate-generation",
        backend="fake",
        provider="fake",
        model="gpt-5-mini",
        request_hash=_HASH,
        started_at="1970-01-01T00:00:00.000000Z",
        completed_at="1970-01-01T00:00:00.000000Z",
        status=LLMCallStatus.SKIPPED,
        external_call_performed=False,
    )
    llm_orchestration_config = LLMOrchestrationConfig(
        run_id="example",
        domain="machine learning",
        candidate_backend="fake",
        reviewer_backend="fake",
        prose_backend="fake",
        write_report=True,
        budget=llm_budget,
    )
    llm_orchestration_step = LLMOrchestrationStep(
        step_name="run-all",
        status=LLMOrchestrationStepStatus.SUCCEEDED,
        summary="Deterministic pipeline completed with fake LLM seams.",
        artifact_ids=["pipeline-run-report"],
        started_at="1970-01-01T00:00:00.000000Z",
        completed_at="1970-01-01T00:00:01.000000Z",
    )
    llm_safety_report = LLMRunSafetyReport(
        run_id="example",
        safe=True,
        warnings=["LLM outputs are not verification evidence."],
    )
    llm_orchestration_report = LLMOrchestrationReport(
        report_id="llm-orchestration-report-example",
        run_id="example",
        config=llm_orchestration_config,
        orchestration_status=(
            LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS
        ),
        steps=[llm_orchestration_step],
        budget_decision=llm_budget_decision,
        budget_usage=llm_usage,
        call_accounting=[llm_call_record],
        safety_report=llm_safety_report,
        selected_backends={
            "candidate_backend": "fake",
            "reviewer_backend": "fake",
            "prose_backend": "fake",
        },
        warnings=["Fake orchestration performs no external LLM calls."],
    )
    llm_orchestration_result = LLMOrchestrationResult(
        run_id="example",
        orchestration_status=llm_orchestration_report.orchestration_status,
        report=llm_orchestration_report,
        full_paper_generation_status="PaperGenerationSucceededWithWarnings",
        paper_release_status="ReadyForHumanReviewWithWarnings",
    )
    planned_output = PlannedOutput(
        output_kind="stage_a_report",
        path="runs/example/reports/stage-a-report.md",
        description="Stage A ranked report.",
    )
    planned_stage = PlannedStage(
        stage_name=PipelineStage.RUN_STAGE_A.value,
        status=PlannedStageStatus.WOULD_RUN,
        reason="No prior run artifacts are required.",
        expected_outputs=[planned_output],
    )
    dry_run_plan = PipelineDryRunPlan(
        run_id="example",
        domain="machine learning",
        method="calibration",
        dry_run_status=DryRunStatus.DRY_RUN_RUNNABLE,
        planned_stages=[planned_stage],
        planned_outputs=[planned_output],
        next_stage=PipelineStage.RUN_STAGE_A,
        selected_stages=[PipelineStage.RUN_STAGE_A],
        warnings_count=0,
        blocking_findings_count=0,
    )
    research_manifest = ResearchObjectManifest(
        research_object_json=artifact,
        research_object_markdown=artifact,
        artifact_manifest=artifact,
        ledger_summary=artifact,
        branch_outcomes=artifact,
        reproducibility_manifest=artifact,
    )
    adapter_config = AdapterConfig()
    targeted_brief = TargetedResearchBrief(
        brief_id="targeted-brief-example",
        title="Calibration under controlled label corruption",
        domain="probabilistic binary classification",
        method="calibration and label-noise robustness",
        central_question=(
            "When does label corruption damage probability calibration more than discrimination?"
        ),
        baseline_candidates=["uncalibrated logistic regression"],
        expected_metrics=["clean-posterior Brier score", "AUROC"],
        negative_controls=["zero label corruption"],
        data_regime="Synthetic data with known clean posterior probabilities.",
        known_risks=["Instance-dependent corruption is not identified without assumptions."],
        allowed_claim_scope="Controlled synthetic settings under declared corruption mechanisms.",
        forbidden_claims=["real-world validation", "novelty proven", "publication ready"],
    )
    kernel_request = KernelRequestEnvelope.model_validate(
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "kernel-request-example",
            "operation": KernelOperation.HASH_CANONICAL_JSON,
            "mode": KernelMode.DEVELOPMENT_COMPATIBILITY,
            "payload": {"value": {"b": 2, "a": 1}},
        }
    )
    kernel_response = KernelResponseEnvelope(
        protocol_version=PROTOCOL_VERSION,
        kernel_version="0.1.0-dev",
        request_id=kernel_request.request_id,
        operation=kernel_request.operation,
        mode=kernel_request.mode,
        status=KernelResponseStatus.ACCEPTED,
        result={
            "canonical_json": '{"a":1,"b":2}',
            "sha256": "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
        },
        diagnostics=[],
        mutation_performed=False,
    )
    kernel_ledger_request = KernelLedgerVerifyRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id="ledger-verify-example",
        operation=KernelOperation.LEDGER_VERIFY,
        mode=KernelMode.DEVELOPMENT_COMPATIBILITY,
        payload={"run_id": "run-example", "commits": []},
    )
    return {
        "adapter-config.example.json": adapter_config.model_dump(mode="json"),
        "candidate.example.json": candidate.model_dump(mode="json"),
        "citation-registry.example.json": citation_registry.model_dump(mode="json"),
        "citation-safety-report.example.json": citation_safety_report.model_dump(
            mode="json"
        ),
        "claim-support-audit.example.json": claim_support_audit.model_dump(mode="json"),
        "artifact.example.json": artifact.model_dump(mode="json"),
        "artifact-manifest.example.json": artifact_manifest.model_dump(mode="json"),
        "stage-result.example.json": stage_result.model_dump(mode="json"),
        "targeted-research-brief.example.json": targeted_brief.model_dump(mode="json"),
        "run-status-report.example.json": run_status.model_dump(mode="json"),
        "resume-validation-report.example.json": resume_validation.model_dump(mode="json"),
        "ledger-tip-validation-report.example.json": ledger_tip.model_dump(mode="json"),
        "llm-orchestration-result.example.json": llm_orchestration_result.model_dump(
            mode="json"
        ),
        "llm-candidate-request.example.json": llm_prompt.model_dump(mode="json"),
        "llm-candidate-response.example.json": llm_response.model_dump(mode="json"),
        "retrieval-query.example.json": retrieval_query.model_dump(mode="json"),
        "retrieval-result.example.json": retrieval.model_dump(mode="json"),
        "proof-contract.example.json": proof_contract.model_dump(mode="json"),
        "proof-result.example.json": proof_result.model_dump(mode="json"),
        "experiment-contract.example.json": experiment_contract.model_dump(mode="json"),
        "experiment-result.example.json": experiment.model_dump(mode="json"),
        "claim.example.json": claim.model_dump(mode="json"),
        "prose-section-contract.example.json": prose_section_contract.model_dump(mode="json"),
        "literature-positioning-report.example.json": (
            literature_positioning_report.model_dump(mode="json")
        ),
        "prose-prompt-contract.example.json": prose_prompt_contract.model_dump(mode="json"),
        "prose-generation-request.example.json": prose_request.model_dump(mode="json"),
        "prose-generation-parse-result.example.json": prose_parse_result.model_dump(mode="json"),
        "prose-safety-report.example.json": prose_safety_report.model_dump(mode="json"),
        "manuscript-drafting-plan.example.json": manuscript_drafting_plan.model_dump(
            mode="json"
        ),
        "section-drafting-result.example.json": section_drafting_result.model_dump(
            mode="json"
        ),
        "complete-markdown-draft.example.json": complete_markdown_draft.model_dump(
            mode="json"
        ),
        "manuscript-drafting-report.example.json": manuscript_drafting_report.model_dump(
            mode="json"
        ),
        "manuscript-assembly-report.example.json": manuscript_assembly_report.model_dump(
            mode="json"
        ),
        "latex-source-map.example.json": latex_source_map.model_dump(mode="json"),
        "latex-render-result.example.json": latex_render_result.model_dump(mode="json"),
        "latex-export-result.example.json": latex_export_result.model_dump(mode="json"),
        "paper-critic-report.example.json": paper_critic_report.model_dump(mode="json"),
        "paper-revision-result.example.json": paper_revision_result.model_dump(mode="json"),
        "full-paper-generation-result.example.json": full_paper_result.model_dump(
            mode="json"
        ),
        "full-paper-golden-bundle.example.json": full_paper_golden_bundle.model_dump(
            mode="json"
        ),
        "full-paper-release-report.example.json": full_paper_release_report.model_dump(
            mode="json"
        ),
        "research-object-manifest.example.json": research_manifest.model_dump(mode="json"),
        "pipeline-dry-run-plan.example.json": dry_run_plan.model_dump(mode="json"),
        "pipeline-run-report.example.json": pipeline_report.model_dump(mode="json"),
        "kernel-request-envelope.example.json": kernel_request.model_dump(mode="json"),
        "kernel-response-envelope.example.json": kernel_response.model_dump(mode="json"),
        "kernel-ledger-request-envelope.example.json": kernel_ledger_request.model_dump(
            mode="json"
        ),
    }


def _expected_files(output_dir: Path) -> dict[Path, str]:
    protocol_root = output_dir.parent
    files = {
        output_dir / definition.filename: _json_text(build_protocol_schema(definition))
        for definition in get_protocol_definitions()
    }
    files[protocol_root / "version.json"] = _json_text(protocol_metadata())
    for filename, example in protocol_examples().items():
        files[protocol_root / "examples" / filename] = _json_text(example)
    return files


def _result(
    output_dir: Path,
    *,
    stale_files: tuple[Path, ...] = (),
) -> ProtocolExportResult:
    protocol_root = output_dir.parent
    return ProtocolExportResult(
        output_dir=output_dir,
        schema_files=tuple(
            output_dir / definition.filename for definition in get_protocol_definitions()
        ),
        example_files=tuple(
            protocol_root / "examples" / filename for filename in protocol_examples()
        ),
        version_file=protocol_root / "version.json",
        stale_files=stale_files,
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _normalize_protocol_schema(schema: dict[str, Any]) -> None:
    """Normalize generated JSON Schema for language-neutral consumers."""
    _normalize_schema_node(schema, property_name=None)


def _normalize_schema_node(node: Any, *, property_name: str | None) -> None:
    if isinstance(node, list):
        for item in node:
            _normalize_schema_node(item, property_name=property_name)
        return
    if not isinstance(node, dict):
        return

    if node.get("format") == "password":
        node.pop("format", None)
        node.pop("writeOnly", None)
        node["x-factori-sensitive"] = True
    elif node.get("format") == "path":
        node.pop("format", None)
        node["x-factori-format"] = "portable-path-string"

    if property_name in _TIMESTAMP_PROPERTY_NAMES:
        _apply_date_time_format(node)

    properties = node.get("properties")
    if isinstance(properties, dict):
        for child_name, child_schema in properties.items():
            _normalize_schema_node(child_schema, property_name=child_name)

    for key, value in node.items():
        if key == "properties":
            continue
        _normalize_schema_node(value, property_name=property_name)


def _apply_date_time_format(node: dict[str, Any]) -> None:
    if node.get("type") == "string":
        node["format"] = "date-time"
        return
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and branch.get("type") == "string":
                    branch["format"] = "date-time"


__all__ = [
    "DEFAULT_PROTOCOL_OUTPUT_DIR",
    "EXAMPLE_PROTOCOLS",
    "ProtocolExportError",
    "ProtocolExportResult",
    "build_protocol_schema",
    "check_protocols",
    "export_protocols",
    "protocol_examples",
    "protocol_metadata",
    "require_protocols_current",
]
