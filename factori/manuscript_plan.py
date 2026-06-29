"""Deterministic manuscript planning skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from factori.abstract_synthesis import load_stage_c_results
from factori.artifacts import ArtifactStore
from factori.claims import build_claim_table
from factori.final_selection import StageCResultItem
from factori.ledger import ResearchLedger
from factori.reports import render_manuscript_plan_report
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BlockedClaim,
    Claim,
    ClaimTable,
    ControllerActionType,
    FinalNucleus,
    FinalNucleusType,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeSectionRole,
    VerificationLabel,
)


class ManuscriptPlanError(RuntimeError):
    """Raised when manuscript planning prerequisites are missing."""


@dataclass(frozen=True)
class ManuscriptPlanningResult:
    """Result of deterministic manuscript planning."""

    run_id: str
    final_nucleus: FinalNucleus
    stage_c_results: list[StageCResultItem]
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    manuscript_plan: ManuscriptPlan
    claim_table_artifact: ArtifactRef
    blocked_claims_artifact: ArtifactRef
    manuscript_plan_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def load_final_nucleus(run_id: str, ledger: ResearchLedger) -> FinalNucleus:
    """Load the latest final nucleus from abstract synthesis."""
    commit = next(
        (
            item
            for item in reversed(ledger.list_commits(run_id))
            if item.action_type == ControllerActionType.FINAL_NUCLEUS_SELECTED
        ),
        None,
    )
    if commit is None:
        raise ManuscriptPlanError(
            "Final nucleus not found; run factori synthesize-abstract first"
        )
    return FinalNucleus.model_validate(commit.payload)


def build_manuscript_plan(
    final_nucleus: FinalNucleus,
    claim_table: ClaimTable,
) -> ManuscriptPlan:
    """Build a deterministic section-level manuscript plan."""
    titles = (
        _abstract_nucleus_sections()
        if final_nucleus.nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS
        else _branch_nucleus_sections()
    )
    blocked_claim_ids = {claim.claim_id for claim in identify_blocked_claims(claim_table)}
    claims_by_section: dict[str, list[str]] = {}
    for claim in claim_table.claims:
        if claim.claim_id in blocked_claim_ids:
            continue
        if claim.allowed_in_main_text or claim.allowed_section == "Future Work":
            claims_by_section.setdefault(claim.allowed_section, []).append(claim.claim_id)

    sections = [
        ManuscriptSectionPlan(
            section_id=_section_id(title),
            title=title,
            bullets=_section_bullets(title, final_nucleus),
            allowed_claim_ids=sorted(
                {
                    claim_id
                    for section_name in _claim_sections_for_title(title)
                    for claim_id in claims_by_section.get(section_name, [])
                }
            ),
            narrative_roles=_section_narrative_roles(title),
        )
        for title in titles
    ]
    allowed_claim_ids = sorted(
        {
            claim_id
            for section in sections
            for claim_id in section.allowed_claim_ids
        }
    )
    omitted_claim_ids = sorted(
        {claim.claim_id for claim in claim_table.claims} - set(allowed_claim_ids)
    )
    return ManuscriptPlan(
        plan_id=f"manuscript-plan-{final_nucleus.id}",
        final_nucleus_id=final_nucleus.id,
        nucleus_type=final_nucleus.nucleus_type,
        title=_plan_title(final_nucleus, claim_table),
        sections=sections,
        allowed_claim_ids=allowed_claim_ids,
        blocked_claim_ids=omitted_claim_ids,
    )


def identify_blocked_claims(claim_table: ClaimTable) -> list[BlockedClaim]:
    """Identify blocked or downgraded claims deterministically."""
    blocked: list[BlockedClaim] = []
    for claim in sorted(claim_table.claims, key=lambda item: item.claim_id):
        reason = _blocked_reason(claim)
        if reason is None:
            continue
        blocked.append(
            BlockedClaim(
                claim_id=claim.claim_id,
                candidate_id=claim.candidate_id,
                claim_text=claim.claim_text,
                claim_label=claim.claim_label,
                blocked_reason=reason,
                downgraded_to=_downgrade_label(claim),
                suggested_section=_suggested_section(claim),
            )
        )
    return blocked


def run_manuscript_planning(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ManuscriptPlanningResult:
    """Run deterministic manuscript planning after abstract synthesis."""
    store.init_run(run_id)
    final_nucleus = load_final_nucleus(run_id, ledger)
    stage_c_results = load_stage_c_results(run_id, ledger)
    artifact_index = _artifact_index(stage_c_results)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.MANUSCRIPT_PLANNING_STARTED,
        payload={
            "final_nucleus_id": final_nucleus.id,
            "stage_c_results": len(stage_c_results),
        },
    )

    claim_table = build_claim_table(final_nucleus, stage_c_results, artifact_index)
    claim_table_artifact = _write_claim_table(run_id, claim_table, store, ledger)
    blocked_claims = identify_blocked_claims(claim_table)
    blocked_claims_artifact = _write_blocked_claims(run_id, blocked_claims, store, ledger)
    manuscript_plan = build_manuscript_plan(final_nucleus, claim_table)
    manuscript_plan_artifact = _write_manuscript_plan(
        run_id,
        manuscript_plan,
        store,
        ledger,
    )
    markdown_artifact = _write_manuscript_plan_markdown(
        run_id=run_id,
        final_nucleus=final_nucleus,
        claim_table=claim_table,
        blocked_claims=blocked_claims,
        manuscript_plan=manuscript_plan,
        store=store,
        ledger=ledger,
    )
    return ManuscriptPlanningResult(
        run_id=run_id,
        final_nucleus=final_nucleus,
        stage_c_results=stage_c_results,
        claim_table=claim_table,
        blocked_claims=blocked_claims,
        manuscript_plan=manuscript_plan,
        claim_table_artifact=claim_table_artifact,
        blocked_claims_artifact=blocked_claims_artifact,
        manuscript_plan_artifact=manuscript_plan_artifact,
        markdown_artifact=markdown_artifact,
    )


def _write_claim_table(
    run_id: str,
    claim_table: ClaimTable,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="claim-table",
        artifact_type=ArtifactType.REPORT,
        data=claim_table,
        metadata={"stage": "manuscript_planning", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.CLAIM_TABLE_BUILT,
        payload=claim_table.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_blocked_claims(
    run_id: str,
    blocked_claims: list[BlockedClaim],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    payload = {"blocked_claims": [claim.model_dump(mode="json") for claim in blocked_claims]}
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="blocked-claims",
        artifact_type=ArtifactType.REPORT,
        data=payload,
        metadata={"stage": "manuscript_planning", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        payload=payload,
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_manuscript_plan(
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="manuscript-plan",
        artifact_type=ArtifactType.REPORT,
        data=manuscript_plan,
        metadata={"stage": "manuscript_planning", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        payload=manuscript_plan.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_manuscript_plan_markdown(
    *,
    run_id: str,
    final_nucleus: FinalNucleus,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    manuscript_plan: ManuscriptPlan,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    markdown = render_manuscript_plan_report(
        run_id=run_id,
        final_nucleus=final_nucleus,
        claim_table=claim_table,
        blocked_claims=blocked_claims,
        manuscript_plan=manuscript_plan,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="manuscript-plan",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "manuscript_planning", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.MANUSCRIPT_PLAN_REPORT_WRITTEN,
        payload={
            "final_nucleus_type": final_nucleus.nucleus_type.value,
            "claims_total": len(claim_table.claims),
            "claims_allowed": len(manuscript_plan.allowed_claim_ids),
            "claims_blocked": len(blocked_claims),
            "manuscript_plan": manuscript_plan.plan_id,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _artifact_index(stage_c_results: list[StageCResultItem]) -> dict[str, ArtifactRef]:
    artifacts: dict[str, ArtifactRef] = {}
    for item in stage_c_results:
        for artifact in item.verification_record.evidence_artifacts:
            artifacts[artifact.id] = artifact
    return artifacts


def _blocked_reason(claim: Claim) -> str | None:
    if "latex" in claim.evidence_types:
        return "LaTeX artifact cannot support a manuscript claim"
    if claim.claim_label == VerificationLabel.LEAN_VERIFIED and "lean" not in claim.evidence_types:
        return "LeanVerified claim lacks proof evidence"
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        if "experiment" not in claim.evidence_types:
            return "SyntheticExperimentVerified claim lacks synthetic experiment evidence"
        if _is_real_world_claim(claim.claim_text):
            return "synthetic evidence cannot support real-world performance claims"
    if claim.claim_label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        return "RealDataExperimentVerified is unavailable in the MVP"
    if claim.claim_label == VerificationLabel.EXPERIMENT_VERIFIED:
        return "generic ExperimentVerified is not an admissible final label"
    if claim.claim_label == VerificationLabel.CONJECTURE:
        text = claim.claim_text.lower()
        if claim.allowed_section not in {"Theory", "Future Work", "Appendix"}:
            return "conjecture cannot be placed as a main theorem or result"
        if "theorem" in text and "conjecture" not in text:
            return "conjecture cannot be stated as a theorem"
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        text = claim.claim_text.lower()
        if claim.allowed_section not in {"Negative Results", "Results", "Limitations"}:
            return "negative result must remain in a negative or limitation section"
        if "negative" not in text and "boundary" not in text:
            return "negative result is not framed as negative or boundary evidence"
    if claim.claim_label == VerificationLabel.LIMITATION and claim.allowed_section != "Limitations":
        return "limitation must remain in the limitations section"
    if (
        claim.claim_label == VerificationLabel.UNSUPPORTED
        and (claim.allowed_in_main_text or claim.allowed_section != "Future Work")
    ):
        return "unsupported claim is blocked from the manuscript body"
    return None


def _downgrade_label(claim: Claim) -> VerificationLabel | None:
    if claim.claim_label in {
        VerificationLabel.LEAN_VERIFIED,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED,
        VerificationLabel.EXPERIMENT_VERIFIED,
    }:
        return VerificationLabel.UNSUPPORTED
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return VerificationLabel.CONJECTURE
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return VerificationLabel.LIMITATION
    return None


def _suggested_section(claim: Claim) -> str | None:
    if claim.claim_label == VerificationLabel.UNSUPPORTED:
        return "Future Work"
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return "Theory"
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return "Negative Results"
    if claim.claim_label == VerificationLabel.LIMITATION:
        return "Limitations"
    return None


_ABSTRACT_NUCLEUS_SECTIONS = (
    "Abstract",
    "Introduction and Problem Framing",
    "Method and Model",
    "Claim and Evidence Boundaries",
    "Demonstration Status",
    "Limitations",
    "Conclusion",
)


_BRANCH_NUCLEUS_SECTIONS = (
    "Abstract",
    "Introduction and Problem Framing",
    "Method and Model",
    "Claim and Evidence Boundaries",
    "Demonstration Status",
    "Limitations",
    "Conclusion",
)


def planned_manuscript_section_count() -> int:
    """Return the deterministic section-task count used by manuscript drafting."""
    counts = {len(_ABSTRACT_NUCLEUS_SECTIONS), len(_BRANCH_NUCLEUS_SECTIONS)}
    if len(counts) != 1:
        raise ManuscriptPlanError(
            "Manuscript section variants have different drafting task counts"
        )
    return counts.pop()


def _abstract_nucleus_sections() -> list[str]:
    return list(_ABSTRACT_NUCLEUS_SECTIONS)


def _branch_nucleus_sections() -> list[str]:
    return list(_BRANCH_NUCLEUS_SECTIONS)


def _claim_sections_for_title(title: str) -> list[str]:
    return {
        "Introduction and Problem Framing": ["Introduction"],
        "Method and Model": ["Model"],
        "Claim and Evidence Boundaries": [
            "Theory",
            "Synthetic Experiments",
            "Results",
            "Negative Results",
            "Limitations",
            "Appendix",
        ],
        "Demonstration Status": ["Synthetic Experiments", "Results"],
        "Limitations": ["Limitations", "Future Work"],
    }.get(title, [title])


def _section_bullets(title: str, final_nucleus: FinalNucleus) -> list[str]:
    if title == "Abstract":
        return [
            "Summarize the problem, central contribution, evidence limitation, and status."
        ]
    if title == "Introduction and Problem Framing":
        return [
            "Make the problem framing explicit.",
            "Do not cite sources unless a citation registry supplies them.",
        ]
    if title == "Method and Model":
        return ["Define objects, assumptions, and admissible claim labels."]
    if title == "Claim and Evidence Boundaries":
        return [
            "State the single bounded central contribution as non-evidence.",
            "Place admitted claims with their evidence links.",
            "Do not upgrade conjectures, fake validators, or presentation artifacts.",
        ]
    if title == "Demonstration Status":
        return [
            "Describe only available synthetic or MVP demonstration status.",
            "Do not claim empirical validation without real experiment evidence.",
        ]
    if title == "Limitations":
        return ["Preserve limitations and unsupported directions without label inflation."]
    return ["Keep section content constrained by the claim table."]


def _section_narrative_roles(title: str) -> list[NarrativeSectionRole]:
    lowered = title.lower()
    roles: list[NarrativeSectionRole] = []
    if "abstract" in lowered:
        roles.append(NarrativeSectionRole.CENTRAL_MESSAGE)
    if "introduction" in lowered or "problem framing" in lowered:
        roles.extend(
            [
                NarrativeSectionRole.PROBLEM_FRAMING,
                NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
            ]
        )
    if "model" in lowered or "setup" in lowered or "method" in lowered:
        roles.append(NarrativeSectionRole.MODEL_FRAME)
    if "claim" in lowered or "evidence" in lowered or "theory" in lowered:
        roles.append(NarrativeSectionRole.MAIN_BODY_RESULT)
    if "demonstration" in lowered or "synthetic" in lowered or "numerical" in lowered:
        roles.append(NarrativeSectionRole.NUMERICAL_VALIDATION)
    if "negative" in lowered or "boundary" in lowered or "limitation" in lowered:
        roles.append(NarrativeSectionRole.SYNTHETIC_BOUNDARY)
    if "limitation" in lowered or "conclusion" in lowered:
        roles.append(NarrativeSectionRole.LIMITATIONS_DISCUSSION)
    if "appendix" in lowered:
        roles.append(NarrativeSectionRole.APPENDIX_ONLY_PROOF)
    return list(dict.fromkeys(roles))


def _section_id(title: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in title)
    return "-".join(part for part in normalized.split("-") if part)


def _plan_title(final_nucleus: FinalNucleus, claim_table: ClaimTable | None = None) -> str:
    if final_nucleus.nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS:
        if final_nucleus.abstract_model is not None:
            mechanism = final_nucleus.abstract_model.mechanism
            return _safe_title(f"Bounded Synthesis of {mechanism}")
        return "Bounded Abstract Synthesis"
    claim_text = ""
    if claim_table is not None and claim_table.claims:
        claim_text = sorted(claim_table.claims, key=lambda item: item.claim_id)[0].claim_text
    if not claim_text:
        return _safe_title(f"Bounded Study of {final_nucleus.id}")
    return _safe_title(_title_from_claim_text(claim_text))


def _title_from_claim_text(claim_text: str) -> str:
    lowered = claim_text.lower().replace("?", " ")
    if "human geography" in lowered:
        if "optimal transport" in lowered:
            return "Optimal Transport for Bounded Structure Analysis in Human Geography"
        if "spatial statistics" in lowered:
            return "Spatial Statistics for Bounded Structure Analysis in Human Geography"
        return "Evidence-Bounded Manuscript Generation for Human Geography Research Candidates"
    fragment = _claim_title_fragment(claim_text)
    fragment = fragment.rstrip(" ?")
    if _fragment_is_grammatically_weak(fragment):
        return "Evidence-Bounded Manuscript Generation for Research Candidates"
    return f"Bounded Study of {fragment}"


def _claim_title_fragment(claim_text: str) -> str:
    fragment = claim_text.split(":")[-1].strip()
    words = [
        word.strip(".,;:()[]{}").lower()
        for word in fragment.replace("/", " ").split()
        if word.strip(".,;:()[]{}")
    ]
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "can",
        "for",
        "how",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    excluded_label_words = {
        "conjecture",
        "conjectural",
        "form",
        "leanverified",
        "syntheticexperimentverified",
        "theorem",
        "verified",
    }
    content_words = [
        word
        for word in words
        if word not in stopwords and word.casefold() not in excluded_label_words
    ]
    selected = content_words[:8] or words[:8] or ["selected", "branch"]
    return " ".join(word.capitalize() for word in selected)


def _fragment_is_grammatically_weak(fragment: str) -> bool:
    lowered = fragment.casefold()
    weak_phrases = (
        " expose ",
        " exposed ",
    )
    return fragment.endswith("?") or any(phrase in f" {lowered} " for phrase in weak_phrases)


def _safe_title(title: str) -> str:
    forbidden = {
        "deterministic branch manuscript plan",
        "untitled",
        "placeholder",
        "draft",
        "paper",
    }
    normalized = " ".join(title.split())
    normalized = normalized.rstrip(" ?")
    if (
        not normalized
        or normalized.casefold() in forbidden
        or _fragment_is_grammatically_weak(normalized)
    ):
        return "Bounded Study of Selected Branch"
    return normalized


def _is_real_world_claim(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["real-world", "real world", "field deployment"])
