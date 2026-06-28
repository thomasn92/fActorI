"""Deterministic prose-generation contracts for future exporters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factori.adapters.prose_prompts import build_prose_section_prompt, forbidden_labels_for_section
from factori.adapters.prose_real import OpenAIProseGenerator
from factori.adapters.prose_safety import validate_generated_section
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import DraftSkeletonError, load_manuscript_planning_artifacts
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.manuscript_plan import ManuscriptPlanError, load_final_nucleus
from factori.narrative_contract import build_narrative_contract
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimTable,
    ControllerActionType,
    FinalAuditReport,
    GeneratedSectionDraft,
    LiteraturePositioningReport,
    ManuscriptPlan,
    NarrativeManuscriptContract,
    PaperSkeleton,
    ProseGenerationContract,
    ProseGenerationParseResult,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    ReleaseGateDecision,
    VerificationLabel,
)

STYLE_CONSTRAINTS = [
    "Preserve claim IDs, labels, and evidence links exactly.",
    "Use scaffold prose only; do not create new scientific claims.",
    "Keep unsupported directions outside normal body sections.",
    "Describe fake deterministic validators as fake MVP validators.",
]

FORBIDDEN_TRANSFORMATIONS = [
    "upgrade Conjecture to theorem",
    "upgrade SyntheticExperimentVerified to real-world validation",
    "upgrade NegativeResult to positive evidence",
    "omit evidence links for main claims",
    "omit blocked-claim appendix",
    "omit limitations",
    "use Markdown or LaTeX as verification evidence",
    "create new scientific claims",
]

BASE_REQUIRED_DISCLAIMERS = [
    "This MVP run used fake deterministic validators, not real Lean or real experiments.",
    "External review readiness is false until real adapters are implemented.",
]


class SectionDraftGenerationError(RuntimeError):
    """Raised when one-section prose generation prerequisites are missing."""


@dataclass(frozen=True)
class SectionDraftResult:
    """Result of one-section prose generation."""

    run_id: str
    section_contract: ProseSectionContract
    prompt_contract: ProsePromptContract
    draft: GeneratedSectionDraft
    parse_result: ProseGenerationParseResult
    safety_report: ProseSafetyReport
    request_artifact: ArtifactRef | None = None
    response_artifact: ArtifactRef | None = None
    draft_artifact: ArtifactRef | None = None
    safety_artifact: ArtifactRef | None = None
    commit_hash: str | None = None


def build_prose_generation_contract(
    run_id: str,
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
    final_audit_report: FinalAuditReport,
    release_gate_decision: ReleaseGateDecision,
) -> ProseGenerationContract:
    """Build a label-preserving contract for future prose generation."""
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    allowed_claims = sorted(
        {
            placeholder.claim_id
            for placeholder in paper_skeleton.claim_placeholders
            if placeholder.claim_label != VerificationLabel.UNSUPPORTED
        }
    )
    blocked_claims = sorted(_blocked_claims_from_appendix(paper_skeleton))
    claim_labels = {
        claim_id: claim_by_id[claim_id].claim_label
        for claim_id in sorted(claim_by_id)
    }
    claim_evidence_links = {
        claim_id: list(claim_by_id[claim_id].evidence_artifact_ids)
        for claim_id in sorted(claim_by_id)
    }
    return ProseGenerationContract(
        run_id=run_id,
        allowed_sections=[section.title for section in paper_skeleton.sections],
        allowed_claims=allowed_claims,
        blocked_claims=blocked_claims,
        claim_labels=claim_labels,
        claim_evidence_links=claim_evidence_links,
        style_constraints=STYLE_CONSTRAINTS,
        forbidden_transformations=FORBIDDEN_TRANSFORMATIONS,
        required_disclaimers=_required_disclaimers(claim_table),
        ready_for_polished_prose=(
            release_gate_decision.ready_for_polished_prose
            and final_audit_report.blocking_failures_count == 0
        ),
    )


def load_section_draft_inputs(
    run_id: str,
    ledger: ResearchLedger,
) -> tuple[ManuscriptPlan, ClaimTable, NarrativeManuscriptContract]:
    """Load manuscript artifacts needed to draft one section."""
    try:
        final_nucleus = load_final_nucleus(run_id, ledger)
        manuscript_plan, claim_table, _blocked_claims = load_manuscript_planning_artifacts(
            run_id,
            ledger,
        )
    except (DraftSkeletonError, ManuscriptPlanError) as exc:
        raise SectionDraftGenerationError(
            "Manuscript planning artifacts not found; run factori plan-manuscript first"
        ) from exc
    narrative_contract = build_narrative_contract(
        manuscript_plan,
        final_nucleus,
        claim_table,
        run_id=run_id,
    )
    return manuscript_plan, claim_table, narrative_contract


def build_prose_section_contract(
    *,
    run_id: str,
    section_id: str,
    manuscript_plan: ManuscriptPlan,
    claim_table: ClaimTable,
    narrative_contract: NarrativeManuscriptContract,
    citation_registry: CitationRegistry | None = None,
    literature_positioning_report: LiteraturePositioningReport | None = None,
    max_words: int = 160,
) -> ProseSectionContract:
    """Build a deterministic section-level prose contract."""
    section = _find_manuscript_section(manuscript_plan, section_id)
    if section is None:
        known = ", ".join(sorted(item.section_id for item in manuscript_plan.sections))
        raise SectionDraftGenerationError(
            f"Section '{section_id}' not found in manuscript plan. Available sections: {known}"
        )
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    allowed_claim_ids = [
        claim_id for claim_id in section.allowed_claim_ids if claim_id in claim_by_id
    ]
    evidence_ids = sorted(
        {
            evidence_id
            for claim_id in allowed_claim_ids
            for evidence_id in claim_by_id[claim_id].evidence_artifact_ids
        }
    )
    source_hashes = {
        "manuscript_plan": sha256_json(manuscript_plan.model_dump(mode="json")),
        "claim_table": sha256_json(claim_table.model_dump(mode="json")),
        "narrative_contract": sha256_json(narrative_contract.model_dump(mode="json")),
    }
    allowed_citations = _allowed_citations_for_section(section, citation_registry)
    if citation_registry is not None:
        source_hashes["citation_registry"] = sha256_json(
            citation_registry.model_dump(mode="json")
        )
    if literature_positioning_report is not None:
        source_hashes["literature_positioning_report"] = sha256_json(
            literature_positioning_report.model_dump(mode="json")
        )
    target_min_words, target_max_words = _target_word_range(section.title, max_words)
    has_experiment_evidence = _has_experiment_evidence(claim_table)
    has_empirical_evidence = _has_empirical_evidence(claim_table)
    has_citation_sources = bool(citation_registry and citation_registry.citations)
    contract = ProseSectionContract(
        run_id=run_id,
        section_id=section.section_id,
        section_title=section.title,
        section_role=section.title,
        narrative_role=section.narrative_roles,
        allowed_claim_ids=allowed_claim_ids,
        allowed_evidence_artifact_ids=evidence_ids,
        allowed_citation_ids=[record.citation_id for record in allowed_citations],
        allowed_citation_keys=[record.citation_key for record in allowed_citations],
        forbidden_claims=sorted(set(manuscript_plan.blocked_claim_ids)),
        evidence_boundary_instructions=[
            "Use only allowed claim IDs.",
            "Use only allowed evidence artifact IDs.",
            "Generated prose is not verification evidence.",
            "Do not invent citations, proofs, experiment results, or empirical validation.",
            "Do not use theorem, conjecture, verified, LeanVerified, "
            "SyntheticExperimentVerified, or RealDataExperimentVerified wording unless the "
            "linked claim and evidence contract explicitly allow it.",
            *(
                [
                    "No real proof or experiment evidence is available for this section; "
                    "state that limitation instead of implying validation."
                ]
                if not has_experiment_evidence
                else []
            ),
            *(
                [
                    "Real-world empirical validation is unavailable for this MVP section."
                ]
                if not has_empirical_evidence
                else []
            ),
        ],
        citation_boundary_instructions=[
            "Use only allowed citation keys.",
            "Citation markers are literature context only.",
            "Do not claim exhaustive literature coverage.",
            "Do not claim retrieval proves novelty.",
            "Do not use citations as proof or experiment evidence.",
            *(
                [
                    "Do not include citation markers because no citation registry sources "
                    "are available for this section."
                ]
                if not has_citation_sources
                else []
            ),
        ],
        literature_positioning_context=(
            literature_positioning_report.model_dump(mode="json")
            if literature_positioning_report is not None
            and allowed_citations
            else None
        ),
        style_instructions=[
            f"Target word range: {target_min_words}-{target_max_words} words.",
            "Use manuscript-draft prose, not final publication-ready prose.",
            "Preserve uncertainty, limitations, and fake-validator disclaimers.",
            "Avoid placeholder headings, placeholder body text, and generic section filler.",
            *(_section_specific_style_instructions(section.title, has_citation_sources)),
        ],
        max_words=max_words,
        required_subsections=_required_subsections(
            section.title,
            has_experiment_evidence=has_experiment_evidence,
            has_citation_sources=has_citation_sources,
        ),
        source_contract_hashes=source_hashes,
    )
    return contract.model_copy(
        update={
            "forbidden_labels": forbidden_labels_for_section(contract, claim_table),
        }
    )


def _find_manuscript_section(
    manuscript_plan: ManuscriptPlan,
    section_id: str,
):
    aliases = {
        "introduction": "introduction-and-problem-framing",
        "problem-framing": "introduction-and-problem-framing",
        "method": "method-and-model",
        "model": "method-and-model",
        "evidence-boundaries": "claim-and-evidence-boundaries",
        "claim-boundaries": "claim-and-evidence-boundaries",
        "demonstration": "demonstration-status",
    }
    normalized = section_id.strip().lower()
    accepted = {normalized, aliases.get(normalized, normalized)}
    return next(
        (item for item in manuscript_plan.sections if item.section_id in accepted),
        None,
    )


def _allowed_citations_for_section(section, citation_registry: CitationRegistry | None):
    if citation_registry is None:
        return []
    role_names = {role.value for role in section.narrative_roles}
    title = section.title.lower()
    if (
        "Introduction" in section.title
        or "literature" in title
        or "background" in title
        or "BackgroundLiteraturePositioning" in role_names
    ):
        return list(citation_registry.citations)
    return []


def _target_word_range(section_title: str, max_words: int) -> tuple[int, int]:
    lower = section_title.lower()
    minimum = 120
    if "abstract" in lower or "conclusion" in lower:
        minimum = 120
    elif "limitation" in lower:
        minimum = 140
    maximum = max(minimum, max_words)
    return minimum, maximum


def _section_specific_style_instructions(
    section_title: str,
    has_citation_sources: bool,
) -> list[str]:
    lower = section_title.lower()
    instructions: list[str] = []
    if "introduction" in lower or "problem framing" in lower:
        instructions.append("Make the problem framing explicit in the first paragraph.")
        if has_citation_sources:
            instructions.append("Use only allowed citation markers for bounded context.")
        else:
            instructions.append(
                "State that no retrieval-backed citations are available rather than "
                "inventing citations."
            )
    if "demonstration" in lower or "result" in lower:
        instructions.append(
            "Describe demonstration status as non-evidence unless linked experiment "
            "evidence exists."
        )
    if "limitation" in lower:
        instructions.append("Include no-evidence and MVP-boundary limitations explicitly.")
    return instructions


def _required_subsections(
    section_title: str,
    *,
    has_experiment_evidence: bool,
    has_citation_sources: bool,
) -> list[str]:
    lower = section_title.lower()
    required: list[str] = []
    if "introduction" in lower or "problem framing" in lower:
        required.append("Problem framing")
        required.append(
            "Bounded literature context"
            if has_citation_sources
            else "No retrieval-backed citations available"
        )
    if ("demonstration" in lower or "result" in lower) and not has_experiment_evidence:
        required.append("No experiment evidence limitation")
    if "limitation" in lower:
        required.append("No publication-readiness claim")
    return required


def _has_experiment_evidence(claim_table: ClaimTable) -> bool:
    return any(
        "experiment" in evidence_type.lower()
        for claim in claim_table.claims
        for evidence_type in claim.evidence_types
    )


def _has_empirical_evidence(claim_table: ClaimTable) -> bool:
    return any(
        "real" in evidence_type.lower()
        for claim in claim_table.claims
        for evidence_type in claim.evidence_types
    )


def build_prose_evidence_map(claim_table: ClaimTable) -> dict[str, dict[str, Any]]:
    """Build a deterministic artifact-id to claim/evidence map."""
    evidence: dict[str, dict[str, Any]] = {}
    links_by_artifact = {
        link.artifact_id: link
        for link in sorted(claim_table.evidence_links, key=lambda item: item.artifact_id)
    }
    for claim in sorted(claim_table.claims, key=lambda item: item.claim_id):
        for evidence_id in claim.evidence_artifact_ids:
            link = links_by_artifact.get(evidence_id)
            evidence[evidence_id] = {
                "artifact_id": evidence_id,
                "claim_id": claim.claim_id,
                "candidate_id": claim.candidate_id,
                "claim_label": claim.claim_label.value,
                "artifact_type": link.artifact_type.value if link is not None else "unknown",
                "evidence_role": link.evidence_role if link is not None else None,
                "supports_label": link.supports_label if link is not None else False,
                "is_verification_evidence": False,
            }
    return evidence


def generate_section_draft(
    *,
    run_id: str,
    section_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    prose_generator,
    write_report: bool = False,
    max_words: int = 160,
) -> SectionDraftResult:
    """Generate and validate one section draft without assembling a full paper."""
    manuscript_plan, claim_table, narrative_contract = load_section_draft_inputs(run_id, ledger)
    section_contract = build_prose_section_contract(
        run_id=run_id,
        section_id=section_id,
        manuscript_plan=manuscript_plan,
        claim_table=claim_table,
        narrative_contract=narrative_contract,
        max_words=max_words,
    )
    evidence_map = build_prose_evidence_map(claim_table)
    backend = getattr(prose_generator, "backend_name", "fake")
    provider = getattr(prose_generator, "provider_name", backend)
    prompt_contract = build_prose_section_prompt(
        section_contract,
        claim_table,
        evidence_map,
        narrative_contract,
        backend=backend,
        provider=provider,
    )
    if isinstance(prose_generator, OpenAIProseGenerator):
        parse_result = prose_generator.generate_section_from_prompt(prompt_contract)
        raw_response = (
            prose_generator.raw_responses[-1] if prose_generator.raw_responses else None
        )
    else:
        draft = prose_generator.generate_section(section_contract, claim_table)
        parse_result = ProseGenerationParseResult(
            section_draft=draft,
            raw_response_type=type(draft).__name__,
            fake=getattr(prose_generator, "is_fake", False),
        )
        raw_response = draft.model_dump(mode="json")
    if parse_result.section_draft is None:
        raise SectionDraftGenerationError(
            "Prose generation did not produce a parseable section draft"
        )
    safety = validate_generated_section(
        parse_result.section_draft,
        section_contract,
        claim_table,
        evidence_map,
    )
    result = SectionDraftResult(
        run_id=run_id,
        section_contract=section_contract,
        prompt_contract=prompt_contract,
        draft=parse_result.section_draft,
        parse_result=parse_result,
        safety_report=safety,
    )
    if not write_report:
        return result
    return _write_section_draft_artifacts(
        run_id=run_id,
        result=result,
        raw_response=raw_response,
        store=store,
        ledger=ledger,
    )


def _write_section_draft_artifacts(
    *,
    run_id: str,
    result: SectionDraftResult,
    raw_response: Any,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> SectionDraftResult:
    section_id = result.section_contract.section_id
    metadata = {
        "stage": "prose_section_draft",
        "artifact_role": "manuscript_prose_context",
        "section_id": section_id,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=f"prose-section-request-{section_id}",
                artifact_type=ArtifactType.REPORT,
                payload=result.prompt_contract,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"prose-section-response-{section_id}",
                artifact_type=ArtifactType.REPORT,
                payload={
                    "raw_response": raw_response,
                    "parse_result": result.parse_result.model_dump(mode="json"),
                    "is_verification_evidence": False,
                },
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"prose-section-draft-{section_id}",
                artifact_type=ArtifactType.REPORT,
                payload=result.draft.content,
                artifact_format="markdown",
                metadata={
                    **metadata,
                    "artifact_role": "presentation_manuscript_draft",
                },
            ),
            ArtifactWriteSpec(
                artifact_id=f"prose-section-safety-{section_id}",
                artifact_type=ArtifactType.REPORT,
                payload=result.safety_report,
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        action_type=ControllerActionType.PROSE_SECTION_DRAFT_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "section_id": section_id,
            "safe": result.safety_report.safe,
            "rejected": result.safety_report.rejected,
            "used_claim_ids": result.safety_report.used_claim_ids,
            "used_evidence_artifact_ids": result.safety_report.used_evidence_artifact_ids,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    artifact_by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return SectionDraftResult(
        run_id=result.run_id,
        section_contract=result.section_contract,
        prompt_contract=result.prompt_contract,
        draft=result.draft,
        parse_result=result.parse_result,
        safety_report=result.safety_report,
        request_artifact=artifact_by_id[f"prose-section-request-{section_id}"],
        response_artifact=artifact_by_id[f"prose-section-response-{section_id}"],
        draft_artifact=artifact_by_id[f"prose-section-draft-{section_id}"],
        safety_artifact=artifact_by_id[f"prose-section-safety-{section_id}"],
        commit_hash=persistence.commit.commit_hash,
    )


def _required_disclaimers(claim_table: ClaimTable) -> list[str]:
    labels = {claim.claim_label for claim in claim_table.claims}
    disclaimers = list(BASE_REQUIRED_DISCLAIMERS)
    if VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED in labels:
        disclaimers.append(
            "Synthetic evidence supports only controlled synthetic assumptions."
        )
    if VerificationLabel.CONJECTURE in labels:
        disclaimers.append("Conjectural statements are not verified theorems.")
    if VerificationLabel.NEGATIVE_RESULT in labels:
        disclaimers.append("Negative results are boundary or failure findings.")
    return disclaimers


def _blocked_claims_from_appendix(paper_skeleton: PaperSkeleton) -> set[str]:
    blocked: set[str] = set()
    for appendix in paper_skeleton.appendices:
        if "Blocked" not in appendix.title:
            continue
        for line in appendix.content_lines:
            claim_id = line.split(":", maxsplit=1)[0].strip()
            if claim_id and claim_id != "none":
                blocked.add(claim_id)
    return blocked
