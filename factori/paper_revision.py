"""Deterministic safe fake revision pass for generated paper drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.citations import (
    CITATION_MARKER_RE,
    build_claim_support_audit,
    repair_confirmed_claim_support_violations,
)
from factori.claim_adjudication import ClaimAdjudicator
from factori.hashing import sha256_text
from factori.ledger import ResearchLedger
from factori.paper_critic import (
    RETRIEVAL_NOVELTY_CLAIMS,
    SYNTHETIC_AS_REAL_CLAIMS,
    build_paper_revision_plan,
    critique_generated_paper,
    critique_paper_from_run,
)
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimSupportAuditReport,
    ControllerActionType,
    PaperCriticReport,
    PaperRevisionActionKind,
    PaperRevisionPatch,
    PaperRevisionPlan,
    PaperRevisionResult,
    PaperRevisionStatus,
    RevisionSafetyReport,
)

REVISION_CONTEXT_WARNING = (
    "Paper revision artifacts are manuscript/revision context only and cannot create "
    "evidence, upgrade labels, or imply publication readiness."
)


@dataclass(frozen=True)
class PaperRevisionRunResult:
    """Result of read-only planning or one persisted fake revision pass."""

    run_id: str
    critic_report: PaperCriticReport
    revision_plan: PaperRevisionPlan
    revision_result: PaperRevisionResult | None = None
    critic_report_artifact: ArtifactRef | None = None
    revision_plan_artifact: ArtifactRef | None = None
    revision_safety_artifact: ArtifactRef | None = None
    revised_markdown_artifact: ArtifactRef | None = None
    safe_repair_report_artifact: ArtifactRef | None = None
    commit_hash: str | None = None


def apply_safe_fake_revision(
    *,
    run_id: str,
    markdown: str,
    revision_plan: PaperRevisionPlan,
    citation_registry: CitationRegistry | None = None,
    bounded_text_repair: bool = False,
    claim_support_audit: ClaimSupportAuditReport | None = None,
) -> PaperRevisionResult:
    """Apply one deterministic conservative text-only revision pass."""
    revised = markdown
    patches: list[PaperRevisionPatch] = []
    if bounded_text_repair and claim_support_audit is not None:
        revised, removed_sentence_ids = repair_confirmed_claim_support_violations(
            revised,
            claim_support_audit,
        )
        patches.extend(
            _patch(
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                sentence_id,
                "[sentence removed by safe repair]",
                "removed confirmed post-adjudication claim-support violation",
            )
            for sentence_id in removed_sentence_ids
        )
    if bounded_text_repair:
        revised, bounded_patches = _apply_bounded_text_repairs(
            revised,
            replace_claim_language=True,
        )
        patches.extend(bounded_patches)
    for action in revision_plan.actions:
        if action == PaperRevisionActionKind.NO_ACTION_NEEDED:
            continue
        if (
            claim_support_audit is not None
            and action == PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE
        ):
            continue
        revised, action_patches = _apply_action(
            revised,
            action,
            citation_registry=citation_registry,
        )
        patches.extend(action_patches)
    revised, central_message_patches = _consolidate_central_message(revised)
    patches.extend(central_message_patches)
    safety = validate_revision_safety(
        run_id=run_id,
        original_markdown=markdown,
        revised_markdown=revised,
        citation_registry=citation_registry,
    )
    status = _revision_status(patches, safety)
    return PaperRevisionResult(
        run_id=run_id,
        revision_status=status,
        critic_report_id=revision_plan.critic_report_id,
        revision_plan_id=revision_plan.plan_id,
        revised_markdown=revised,
        patches=_with_patch_ids(patches),
        safety_report=safety,
    )


def validate_revision_safety(
    *,
    run_id: str,
    original_markdown: str,
    revised_markdown: str,
    citation_registry: CitationRegistry | None = None,
) -> RevisionSafetyReport:
    """Validate that a revised draft did not invent evidence, labels, or citations."""
    reasons: list[str] = []
    warnings = [REVISION_CONTEXT_WARNING]
    allowed_citations = (
        {record.citation_key for record in citation_registry.citations}
        if citation_registry is not None
        else set()
    )
    original_keys = set(CITATION_MARKER_RE.findall(original_markdown))
    revised_keys = set(CITATION_MARKER_RE.findall(revised_markdown))
    invented = (
        sorted(revised_keys - allowed_citations)
        if citation_registry
        else sorted(revised_keys)
    )
    if invented:
        reasons.append(
            "revision introduced or retained unknown citation keys: "
            + ", ".join(invented)
        )
    lower = _normalized(revised_markdown)
    if any(phrase in lower for phrase in RETRIEVAL_NOVELTY_CLAIMS):
        reasons.append("revision still describes retrieval or citations as novelty/proof evidence")
    if any(phrase in lower for phrase in SYNTHETIC_AS_REAL_CLAIMS):
        reasons.append("revision still describes synthetic evidence as real-world validation")
    forbidden_labels = [
        "leanverified",
        "syntheticexperimentverified",
        "realdataexperimentverified",
        "empiricallyvalidated",
        "realworldvalidated",
    ]
    created_or_upgraded = any(label in lower for label in forbidden_labels)
    if created_or_upgraded:
        reasons.append("revision contains unsupported verification-label language")
    if "publication ready" in lower or "publication-ready" in lower:
        reasons.append("revision implies publication readiness")
    known_preserved = sorted(original_keys & revised_keys & allowed_citations)
    return RevisionSafetyReport(
        run_id=run_id,
        safe=not reasons,
        rejected=bool(reasons),
        reasons=sorted(set(reasons)),
        warnings=warnings,
        invented_citation_keys=invented,
        known_citation_keys_preserved=known_preserved,
        created_or_upgraded_labels=created_or_upgraded,
        mutated_claim_table=False,
        mutated_evidence_map=False,
    )


def revise_paper_from_run(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    apply_safe_fake_revision_flag: bool = False,
    write_report: bool = False,
    safe_repair_mode: bool = False,
    claim_adjudicator: ClaimAdjudicator | None = None,
) -> PaperRevisionRunResult:
    """Plan or apply one safe fake paper revision pass for the latest draft."""
    critic = critique_paper_from_run(
        run_id=run_id,
        root=root,
        store=store,
        ledger=ledger,
        write_report=False,
    )
    plan = build_paper_revision_plan(critic.critic_report)
    if not apply_safe_fake_revision_flag:
        return PaperRevisionRunResult(
            run_id=run_id,
            critic_report=critic.critic_report,
            revision_plan=plan,
        )
    claim_support_audit = (
        build_claim_support_audit(
            run_id=run_id,
            markdown=critic.inputs.markdown,
            citation_registry=critic.inputs.citation_registry,
            claim_adjudicator=claim_adjudicator,
        )
        if safe_repair_mode and claim_adjudicator is not None
        else None
    )
    revision_result = apply_safe_fake_revision(
        run_id=run_id,
        markdown=critic.inputs.markdown,
        revision_plan=plan,
        citation_registry=critic.inputs.citation_registry,
        bounded_text_repair=safe_repair_mode,
        claim_support_audit=claim_support_audit,
    )
    repaired_critic = critic.critic_report
    if safe_repair_mode:
        repaired_critic = critique_generated_paper(
            run_id=run_id,
            markdown=revision_result.revised_markdown,
            citation_registry=critic.inputs.citation_registry,
            latex_result=critic.inputs.latex_result,
            source_map=critic.inputs.source_map,
            latex_safety_report=critic.inputs.latex_safety_report,
            manuscript_draft_artifact_id="revised-manuscript-draft",
            latex_artifact_id=(
                critic.inputs.latex_artifact.id
                if critic.inputs.latex_artifact is not None
                else None
            ),
            source_map_artifact_id=(
                critic.inputs.source_map_artifact.id
                if critic.inputs.source_map_artifact is not None
                else None
            ),
        )
    result = PaperRevisionRunResult(
        run_id=run_id,
        critic_report=repaired_critic,
        revision_plan=plan,
        revision_result=revision_result,
    )
    if not write_report:
        return result
    persistence = write_paper_revision_artifacts(
        run_id=run_id,
        store=store,
        ledger=ledger,
        critic_report=repaired_critic,
        pre_repair_critic_report=critic.critic_report,
        revision_plan=plan,
        revision_result=revision_result,
        safe_repair_mode=safe_repair_mode,
        original_markdown=critic.inputs.markdown,
    )
    return _with_persisted_artifacts(result, persistence)


def write_paper_revision_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    critic_report: PaperCriticReport,
    revision_plan: PaperRevisionPlan,
    revision_result: PaperRevisionResult,
    pre_repair_critic_report: PaperCriticReport | None = None,
    safe_repair_mode: bool = False,
    original_markdown: str | None = None,
) -> PersistenceResult:
    """Persist critic, plan, safety, and revised draft artifacts as context only."""
    metadata = _paper_revision_metadata()
    critic_artifact_id = (
        "safe-repair-critic-report" if safe_repair_mode else "paper-critic-report"
    )
    revision_result = revision_result.model_copy(
        update={
            "critic_report_artifact_id": critic_artifact_id,
            "revision_plan_artifact_id": "paper-revision-plan",
            "revision_safety_artifact_id": "revision-safety-report",
            "revised_markdown_artifact_id": "revised-manuscript-draft",
        }
    )
    artifact_specs = [
        ArtifactWriteSpec(
            artifact_id=critic_artifact_id,
            artifact_type=ArtifactType.REPORT,
            payload=critic_report,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="paper-revision-plan",
            artifact_type=ArtifactType.REPORT,
            payload=revision_plan,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="revision-safety-report",
            artifact_type=ArtifactType.REPORT,
            payload=revision_result.safety_report,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="revised-manuscript-draft",
            artifact_type=ArtifactType.REPORT,
            payload=revision_result.revised_markdown,
            artifact_format="markdown",
            metadata={**metadata, "artifact_role": "paper_revision_presentation_draft"},
        ),
        ArtifactWriteSpec(
            artifact_id="paper-revision-result",
            artifact_type=ArtifactType.REPORT,
            payload=revision_result,
            artifact_format="json",
            metadata=metadata,
        ),
    ]
    if safe_repair_mode:
        artifact_specs.append(
            ArtifactWriteSpec(
                artifact_id="safe-repair-report",
                artifact_type=ArtifactType.REPORT,
                payload=_safe_repair_report(
                    run_id=run_id,
                    original_markdown=original_markdown or "",
                    revision_plan=revision_plan,
                    revision_result=revision_result,
                    pre_repair_critic_report=pre_repair_critic_report,
                    post_repair_critic_report=critic_report,
                ),
                artifact_format="json",
                metadata={**metadata, "artifact_role": "safe_repair_audit_context"},
            )
        )
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.PAPER_REVISION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "revision_status": revision_result.revision_status.value,
            "patches": len(revision_result.patches),
            "safe": revision_result.safety_report.safe,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _apply_action(
    markdown: str,
    action: PaperRevisionActionKind,
    *,
    citation_registry: CitationRegistry | None,
) -> tuple[str, list[PaperRevisionPatch]]:
    if action == PaperRevisionActionKind.ADD_CENTRAL_MESSAGE:
        return _insert_after_heading(
            markdown,
            "Claim and Evidence Boundaries",
            (
                "**Central message.** The bounded contribution of this draft is to "
                "organize approved manuscript artifacts for human review while "
                "preserving proof, experiment, citation, and publication-readiness "
                "boundaries. This statement is manuscript context, not evidence."
            ),
            action=action,
        )
    if action == PaperRevisionActionKind.CLARIFY_PROBLEM_STATEMENT:
        return _insert_after_heading(
            markdown,
            "Introduction",
            "Problem framing is stated as a deterministic placeholder for revision only.",
            action,
        )
    if action in {
        PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP,
        PaperRevisionActionKind.ADD_CITATION_LIMITATION,
    }:
        return _insert_after_heading(
            markdown,
            "Introduction",
            (
                "Literature positioning is bounded by the retrieved citation metadata and is "
                "non-exhaustive; it does not prove novelty."
            ),
            action,
        )
    if action == PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE:
        return _replace_unsafe_claim_language(markdown, action)
    if action == PaperRevisionActionKind.REMOVE_INVENTED_CITATION:
        return _remove_unknown_citations(markdown, citation_registry, action)
    if action == PaperRevisionActionKind.CLARIFY_SYNTHETIC_ONLY_BOUNDARY:
        return _clarify_synthetic_boundary(markdown, action)
    if action == PaperRevisionActionKind.ADD_MISSING_LIMITATION:
        return _ensure_section(
            markdown,
            heading="Limitations",
            body=(
                "This deterministic revision records missing limitations. Presentation drafts "
                "do not create evidence, empirical validation, or publication readiness."
            ),
            action=action,
        )
    if action == PaperRevisionActionKind.ADD_SOURCE_MAP_WARNING:
        return _ensure_section(
            markdown,
            heading="Source Map Notes",
            body=(
                "LaTeX source-map coverage should be regenerated from the revised Markdown "
                "before presentation review."
            ),
            action=action,
        )
    if action == PaperRevisionActionKind.MOVE_TECHNICAL_LEMMA_TO_APPENDIX:
        return _ensure_section(
            markdown,
            heading="Appendix",
            body="Technical lemmas should be allocated here rather than expanded in the main body.",
            action=action,
        )
    return markdown, []


def _apply_bounded_text_repairs(
    markdown: str,
    *,
    replace_claim_language: bool = True,
) -> tuple[str, list[PaperRevisionPatch]]:
    action = PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE
    revised = markdown
    patches: list[PaperRevisionPatch] = []
    unsafe_placeholder = re.compile(
        r"^\[UNSAFE SECTION OMITTED\].*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    unsupported_sentence = re.compile(
        r"^\[UNSUPPORTED SENTENCE\].*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    replacement = (
        "[SECTION OMITTED BY SAFETY CHECK] Generated text was excluded. "
        "No evidence or scientific validation is asserted."
    )
    for pattern, rationale in (
        (unsafe_placeholder, "removed unsafe generated section text"),
        (unsupported_sentence, "removed explicitly unsupported assertive sentence"),
    ):
        updated, count = pattern.subn(replacement, revised)
        if count:
            patches.append(_patch(action, pattern.pattern, replacement, rationale))
            revised = updated
    if replace_claim_language:
        revised, claim_patches = _replace_unsafe_claim_language(revised, action)
        patches.extend(claim_patches)
    revised, boundary_patches = _clarify_synthetic_boundary(
        revised,
        PaperRevisionActionKind.CLARIFY_SYNTHETIC_ONLY_BOUNDARY,
    )
    patches.extend(boundary_patches)
    revised, limitation_patches = _insert_after_heading(
        revised,
        "Limitations",
        (
            "Safe-repair note: omitted or downgraded generated text is presentation-only. "
            "MVP and synthetic outputs do not establish empirical validation, scientific "
            "validation, or publication readiness."
        ),
        PaperRevisionActionKind.ADD_MISSING_LIMITATION,
    )
    patches.extend(limitation_patches)
    return revised, patches


def _ensure_section(
    markdown: str,
    *,
    heading: str,
    body: str,
    action: PaperRevisionActionKind,
) -> tuple[str, list[PaperRevisionPatch]]:
    if f"## {heading}".lower() in markdown.lower():
        return markdown, []
    insertion = f"\n\n## {heading}\n\n{body}\n"
    target = "## Claim/Evidence Appendix"
    if target in markdown:
        revised = markdown.replace(target, insertion.strip() + "\n\n" + target, 1)
    else:
        revised = markdown.rstrip() + insertion
    return revised, [_patch(action, "", insertion.strip(), f"inserted {heading} section")]


def _consolidate_central_message(
    markdown: str,
) -> tuple[str, list[PaperRevisionPatch]]:
    pattern = re.compile(r"^##\s+Central Message\s*$", flags=re.IGNORECASE | re.MULTILINE)
    if not pattern.search(markdown):
        return markdown, []
    replacement = "**Central message.**"
    revised = pattern.sub(replacement, markdown)
    return revised, [
        _patch(
            PaperRevisionActionKind.ADD_CENTRAL_MESSAGE,
            "## Central Message",
            replacement,
            "demoted standalone central-message heading into manuscript text",
        )
    ]


def _insert_after_heading(
    markdown: str,
    heading: str,
    text: str,
    action: PaperRevisionActionKind,
) -> tuple[str, list[PaperRevisionPatch]]:
    marker = f"## {heading}"
    if text in markdown:
        return markdown, []
    if marker not in markdown:
        return _ensure_section(markdown, heading=heading, body=text, action=action)
    revised = markdown.replace(marker, f"{marker}\n\n{text}", 1)
    return revised, [_patch(action, marker, f"{marker}\n\n{text}", f"inserted {heading} note")]


def _replace_unsafe_claim_language(
    markdown: str,
    action: PaperRevisionActionKind,
) -> tuple[str, list[PaperRevisionPatch]]:
    replacements = {
        "retrieval proves novelty": (
            "retrieval provides bounded context relative to retrieved sources"
        ),
        "citations prove novelty": (
            "citations provide bounded context relative to retrieved sources"
        ),
        "novelty is proven by retrieval": (
            "novelty is positioned only relative to retrieved sources"
        ),
        "novelty is proven": "novelty is framed as a bounded positioning claim",
        "LeanVerified": "proof-supported only with linked proof evidence",
        "SyntheticExperimentVerified": (
            "synthetic-experiment-supported only with linked synthetic evidence"
        ),
        "RealDataExperimentVerified": "real-data support is unavailable in this MVP",
        "EmpiricallyValidated": "validation limited to stated non-empirical evidence",
        "RealWorldValidated": "support is limited to the stated non-empirical setting",
        "Conjecture": "unverified claim",
        "Theorem": "formal claim without verification authority",
        "publication-ready": "ready for human review only",
        "publication ready": "ready for human review only",
    }
    revised = markdown
    patches: list[PaperRevisionPatch] = []
    for before, after in replacements.items():
        if before.lower() in revised.lower():
            revised = _replace_case_insensitive(revised, before, after)
            patches.append(_patch(action, before, after, "downgraded unsupported claim language"))
    return revised, patches


def _clarify_synthetic_boundary(
    markdown: str,
    action: PaperRevisionActionKind,
) -> tuple[str, list[PaperRevisionPatch]]:
    replacements = {
        "empirically validated": "validated only in the stated synthetic setting",
        "real-world validation": "synthetic-only validation boundary",
        "real world validation": "synthetic-only validation boundary",
        "real-world validated": "validated only in the stated synthetic setting",
        "validated on real data": "validated only in the stated synthetic setting",
    }
    revised = markdown
    patches: list[PaperRevisionPatch] = []
    for before, after in replacements.items():
        if before in revised.lower():
            revised = _replace_case_insensitive(revised, before, after)
            patches.append(_patch(action, before, after, "clarified synthetic-only boundary"))
    return revised, patches


def _remove_unknown_citations(
    markdown: str,
    citation_registry: CitationRegistry | None,
    action: PaperRevisionActionKind,
) -> tuple[str, list[PaperRevisionPatch]]:
    allowed = (
        {record.citation_key for record in citation_registry.citations}
        if citation_registry is not None
        else set()
    )
    revised = markdown
    patches: list[PaperRevisionPatch] = []
    for key in sorted(set(CITATION_MARKER_RE.findall(markdown))):
        if key in allowed:
            continue
        before = f"[@{key}]"
        after = f"[citation removed: {key} was not in the citation registry]"
        revised = revised.replace(before, after)
        patches.append(_patch(action, before, after, "removed invented citation marker"))
    return revised, patches


def _replace_case_insensitive(text: str, before: str, after: str) -> str:
    import re

    return re.sub(re.escape(before), after, text, flags=re.IGNORECASE)


def _patch(
    action: PaperRevisionActionKind,
    before: str,
    after: str,
    rationale: str,
) -> PaperRevisionPatch:
    return PaperRevisionPatch(
        patch_id="pending",
        action=action,
        before_snippet=before,
        after_snippet=after,
        rationale=rationale,
    )


def _with_patch_ids(patches: list[PaperRevisionPatch]) -> list[PaperRevisionPatch]:
    return [
        patch.model_copy(update={"patch_id": f"paper-revision-patch-{index:03d}"})
        for index, patch in enumerate(patches, start=1)
    ]


def _revision_status(
    patches: list[PaperRevisionPatch],
    safety: RevisionSafetyReport,
) -> PaperRevisionStatus:
    if safety.rejected:
        return PaperRevisionStatus.REVISION_BLOCKED_UNSAFE
    if not patches:
        return PaperRevisionStatus.NO_REVISION_NEEDED
    if safety.warnings:
        return PaperRevisionStatus.REVISION_APPLIED_WITH_WARNINGS
    return PaperRevisionStatus.REVISION_APPLIED


def _paper_revision_metadata() -> dict[str, object]:
    return {
        "stage": "paper_revision",
        "artifact_role": "paper_revision_manuscript_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


def _safe_repair_report(
    *,
    run_id: str,
    original_markdown: str,
    revision_plan: PaperRevisionPlan,
    revision_result: PaperRevisionResult,
    pre_repair_critic_report: PaperCriticReport | None = None,
    post_repair_critic_report: PaperCriticReport | None = None,
) -> dict[str, object]:
    forbidden_phrases = (
        "Conjecture",
        "Theorem",
        "LeanVerified",
        "SyntheticExperimentVerified",
        "RealDataExperimentVerified",
        "EmpiricallyValidated",
        "RealWorldValidated",
        "publication ready",
        "publication-ready",
    )
    repaired = revision_result.revised_markdown
    pre_repair_warnings = _repair_warning_bucket(
        original_markdown,
        pre_repair_critic_report,
    )
    post_repair_warnings = _repair_warning_bucket(
        repaired,
        post_repair_critic_report,
        extra=[
            *revision_result.safety_report.warnings,
            *revision_result.safety_report.reasons,
        ],
    )
    removed = sorted(
        phrase
        for phrase in forbidden_phrases
        if phrase.lower() in original_markdown.lower()
        and phrase.lower() not in repaired.lower()
    )
    return {
        "report_id": f"safe-repair-report-{run_id}",
        "run_id": run_id,
        "repairs_attempted": [
            "BoundedTextSafetyRepair",
            *[action.value for action in revision_plan.actions],
        ],
        "repairs_applied": len(revision_result.patches),
        "forbidden_phrases_removed": removed,
        "sentences_removed_or_downgraded": sorted(
            {patch.rationale for patch in revision_result.patches}
        ),
        "pre_repair_warnings": pre_repair_warnings,
        "repaired_warnings": sorted(
            set(pre_repair_warnings).difference(post_repair_warnings)
        ),
        "post_repair_warnings": post_repair_warnings,
        "before_content_hash": sha256_text(original_markdown),
        "after_content_hash": sha256_text(repaired),
        "invented_citations": bool(
            revision_result.safety_report.invented_citation_keys
        ),
        "invented_citation_keys": revision_result.safety_report.invented_citation_keys,
        "created_or_upgraded_labels": (
            revision_result.safety_report.created_or_upgraded_labels
        ),
        "mutated_claim_table": False,
        "mutated_evidence_map": False,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


def _repair_warning_bucket(
    markdown: str,
    critic_report: PaperCriticReport | None,
    *,
    extra: list[str] | None = None,
) -> list[str]:
    warnings = list(extra or [])
    warnings.extend(_unsafe_placeholder_warnings(markdown))
    if critic_report is not None:
        warnings.extend(
            finding.message
            for finding in critic_report.findings
            if finding.severity.value in {"Warning", "Major", "Blocking"}
        )
    return sorted({warning for warning in warnings if warning})


def _unsafe_placeholder_warnings(markdown: str) -> list[str]:
    warnings: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("[unsafe section omitted]"):
            continue
        message = stripped.split("]", 1)[-1].strip()
        warnings.extend(part.strip() for part in message.split(";") if part.strip())
    return warnings


def _with_persisted_artifacts(
    result: PaperRevisionRunResult,
    persistence: PersistenceResult,
) -> PaperRevisionRunResult:
    artifacts = {artifact.id: artifact for artifact in persistence.artifacts}
    revision_result = result.revision_result
    if revision_result is not None:
        revision_result = revision_result.model_copy(
            update={
                "critic_report_artifact_id": (
                    "safe-repair-critic-report"
                    if "safe-repair-critic-report" in artifacts
                    else "paper-critic-report"
                ),
                "revision_plan_artifact_id": "paper-revision-plan",
                "revision_safety_artifact_id": "revision-safety-report",
                "revised_markdown_artifact_id": "revised-manuscript-draft",
            }
        )
    return PaperRevisionRunResult(
        run_id=result.run_id,
        critic_report=result.critic_report,
        revision_plan=result.revision_plan,
        revision_result=revision_result,
        critic_report_artifact=(
            artifacts.get("safe-repair-critic-report")
            or artifacts.get("paper-critic-report")
        ),
        revision_plan_artifact=artifacts.get("paper-revision-plan"),
        revision_safety_artifact=artifacts.get("revision-safety-report"),
        revised_markdown_artifact=artifacts.get("revised-manuscript-draft"),
        safe_repair_report_artifact=artifacts.get("safe-repair-report"),
        commit_hash=persistence.commit.commit_hash,
    )


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


__all__ = [
    "REVISION_CONTEXT_WARNING",
    "PaperRevisionRunResult",
    "apply_safe_fake_revision",
    "build_paper_revision_plan",
    "revise_paper_from_run",
    "validate_revision_safety",
    "write_paper_revision_artifacts",
]
