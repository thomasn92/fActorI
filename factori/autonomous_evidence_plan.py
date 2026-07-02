"""Deterministic autonomous evidence-gap planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.claim_evidence import (
    BOUNDED_EMPIRICAL_CLAIM_CLASSES,
    BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
    claim_evidence_summary_fields,
    latest_claim_evidence_map_path,
    latest_claim_support_audit_path,
)
from factori.gap_attempts import (
    annotate_plan_items_with_history,
    load_latest_gap_attempt_history,
)
from factori.ledger import ResearchLedger
from factori.persistence import (
    ArtifactWriteSpec,
    PersistenceResult,
    persist_artifacts_with_commit,
)
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ClaimSupportAuditReport,
    ControllerActionType,
    RetrievalQualityReport,
)

_PLANNER_BACKENDS = {"off", "deterministic", "fake", "openai"}
_SUPPORTED_STATUSES = {"supported_within_scope", "not_required_scaffold"}
_FORMAL_PROOF_SUPPORT = "formal_proof_verification"
_EXPERIMENT_SUPPORT = "experiment_result"


class AutonomousEvidencePlanError(RuntimeError):
    """Raised when an autonomous evidence-gap plan cannot be built or inspected."""


@dataclass(frozen=True)
class AutonomousEvidenceGapPlanPersistResult:
    """Persisted autonomous evidence-gap plan and reviewer summary."""

    run_id: str
    plan: AutonomousEvidenceGapPlan
    persistence: PersistenceResult
    plan_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    reviewer_summary_artifact: ArtifactRef
    reviewer_summary_markdown_artifact: ArtifactRef


def build_autonomous_evidence_gap_plan(
    *,
    run_id: str,
    root: str | Path = ".",
    backend: str = "off",
    model: str | None = None,
    max_calls: int = 0,
    allow_external_calls: bool = False,
    max_attempts_per_gap: int = 2,
) -> AutonomousEvidenceGapPlan:
    """Build a deterministic autonomous next-action plan without mutation."""
    del model
    if backend not in _PLANNER_BACKENDS:
        raise AutonomousEvidencePlanError(
            "autonomous evidence planner backend must be off, deterministic, fake, or openai"
        )
    if max_calls < 0:
        raise AutonomousEvidencePlanError(
            "max autonomous evidence planner calls must be non-negative"
        )
    if max_attempts_per_gap < 1:
        raise AutonomousEvidencePlanError("max attempts per gap must be at least 1")
    if backend == "off":
        raise AutonomousEvidencePlanError(
            "Autonomous evidence-gap planning is disabled; select deterministic or fake."
        )
    if backend == "openai":
        if not allow_external_calls:
            raise AutonomousEvidencePlanError(
                "OpenAI autonomous evidence planning requires --allow-external-calls."
            )
        raise AutonomousEvidencePlanError(
            "OpenAI autonomous evidence planning is gated but not implemented in M66."
        )

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise AutonomousEvidencePlanError(f"No run directory found for run_id={run_id}.")

    ledger_report = validate_ledger_tip(run_id, root=root_path)
    if ledger_report.blocking_findings:
        reason = "Ledger validation has blocking findings; autonomous planning is unsafe."
        return _blocked_plan(
            run_id=run_id,
            backend=backend,
            status="blocked_invalid_ledger",
            reason=reason,
            claim_support_path=_path_if_exists(
                latest_claim_support_audit_path(root_path, run_id),
                root_path,
            ),
            retrieval_quality_path=_path_if_exists(
                reports / "retrieval-quality-report.json",
                root_path,
            ),
        )

    claim_map_path = latest_claim_evidence_map_path(root_path, run_id)
    claim_support_path = _path_if_exists(
        latest_claim_support_audit_path(root_path, run_id),
        root_path,
    )
    retrieval_quality_path = _path_if_exists(
        reports / "retrieval-quality-report.json",
        root_path,
    )
    if claim_map_path is None:
        return _blocked_plan(
            run_id=run_id,
            backend=backend,
            status="blocked_missing_claim_evidence_map",
            reason=(
                "Claim-evidence map is missing; run build-claim-evidence-map before "
                "autonomous planning."
            ),
            claim_support_path=claim_support_path,
            retrieval_quality_path=retrieval_quality_path,
        )
    try:
        claim_map = ClaimEvidenceMap.model_validate_json(
            claim_map_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return _blocked_plan(
            run_id=run_id,
            backend=backend,
            status="blocked_corrupt_claim_evidence_map",
            reason=(
                "Claim-evidence map is corrupt or unreadable; autonomous planning "
                "requires a valid scoped map."
            ),
            claim_map_path=claim_map_path.relative_to(root_path).as_posix(),
            claim_support_path=claim_support_path,
            retrieval_quality_path=retrieval_quality_path,
        )

    contradiction = _claim_map_contradiction(claim_map)
    if contradiction is not None:
        return _blocked_plan(
            run_id=run_id,
            backend=backend,
            status="blocked_contradictory_claim_evidence_map",
            reason=contradiction,
            claim_map_path=claim_map_path.relative_to(root_path).as_posix(),
            claim_support_path=claim_support_path,
            retrieval_quality_path=retrieval_quality_path,
        )

    retrieval_quality = _read_model(
        reports / "retrieval-quality-report.json",
        RetrievalQualityReport,
    )
    _ = _read_model(latest_claim_support_audit_path(root_path, run_id), ClaimSupportAuditReport)
    items = _plan_items_for_claim_map(claim_map)
    items.extend(
        _bundle_level_plan_items(
            claim_map=claim_map,
            retrieval_quality=retrieval_quality,
            evidence_aware_refresh_present=(
                reports / "evidence-aware-refresh-report.json"
            ).is_file(),
            first_index=len(items) + 1,
        )
    )
    history = load_latest_gap_attempt_history(root_path, run_id)
    from factori.gap_strategy_diversification import (  # noqa: PLC0415
        selected_strategy_plan_items,
    )

    items.extend(
        selected_strategy_plan_items(
            root=root_path,
            run_id=run_id,
            history=history,
        )
    )
    items = [
        item.model_copy(update={"item_id": f"plan-item-{index:03d}"})
        for index, item in enumerate(items, start=1)
    ]
    items = annotate_plan_items_with_history(
        run_id=run_id,
        items=items,
        history=history,
    )
    return _finalize_plan(
        run_id=run_id,
        backend=backend,
        status="planned",
        claim_map_path=claim_map_path.relative_to(root_path).as_posix(),
        claim_support_path=claim_support_path,
        retrieval_quality_path=retrieval_quality_path,
        items=items,
        requires_human_intervention=False,
        human_reason=None,
    )


def persist_autonomous_evidence_gap_plan(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    backend: str = "off",
    model: str | None = None,
    max_calls: int = 0,
    allow_external_calls: bool = False,
    max_attempts_per_gap: int = 2,
) -> AutonomousEvidenceGapPlanPersistResult:
    """Persist an autonomous evidence-gap plan and refreshed reviewer summary."""
    root_path = Path(root)
    plan = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=root_path,
        backend=backend,
        model=model,
        max_calls=max_calls,
        allow_external_calls=allow_external_calls,
        max_attempts_per_gap=max_attempts_per_gap,
    )
    plan_id = _next_plan_id(root_path, run_id)
    reviewer_summary_id = _next_plan_reviewer_summary_id(root_path, run_id)

    # Local import avoids a module import cycle with full-paper inspection helpers.
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    reviewer_summary = build_reviewer_bundle_summary(
        run_id=run_id,
        root=root_path,
        autonomous_evidence_plan=plan,
    )
    reviewer_summary_markdown = render_reviewer_bundle_summary_markdown(
        reviewer_summary
    )
    metadata = {
        "stage": "autonomous_evidence_gap_planning",
        "artifact_role": "autonomous_evidence_gap_plan_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    reviewer_metadata = {
        **metadata,
        "artifact_role": "reviewer_bundle_summary_context",
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                plan_id,
                ArtifactType.REPORT,
                plan,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                f"{plan_id}-markdown",
                ArtifactType.REPORT,
                render_autonomous_evidence_gap_plan_markdown(plan),
                "markdown",
                metadata,
                filename_stem=plan_id,
            ),
            ArtifactWriteSpec(
                reviewer_summary_id,
                ArtifactType.REPORT,
                reviewer_summary,
                "json",
                reviewer_metadata,
            ),
            ArtifactWriteSpec(
                f"{reviewer_summary_id}-markdown",
                ArtifactType.REPORT,
                reviewer_summary_markdown,
                "markdown",
                reviewer_metadata,
                filename_stem=reviewer_summary_id,
            ),
        ],
        action_type=ControllerActionType.AUTONOMOUS_EVIDENCE_PLAN_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "planner_backend": plan.planner_backend,
            "planner_status": plan.planner_status,
            **autonomous_evidence_plan_summary_fields(plan),
            "publication_ready": False,
            "reviewer_summary_updated": True,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousEvidenceGapPlanPersistResult(
        run_id=run_id,
        plan=plan,
        persistence=persistence,
        plan_artifact=by_id[plan_id],
        markdown_artifact=by_id[f"{plan_id}-markdown"],
        reviewer_summary_artifact=by_id[reviewer_summary_id],
        reviewer_summary_markdown_artifact=by_id[f"{reviewer_summary_id}-markdown"],
    )


def inspect_autonomous_evidence_gap_plan(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect the latest autonomous evidence-gap plan without mutation."""
    root_path = Path(root)
    path = latest_autonomous_evidence_gap_plan_path(root_path, run_id)
    if path is None:
        raise AutonomousEvidencePlanError(
            f"No autonomous evidence-gap plan found for run_id={run_id}."
        )
    try:
        plan = AutonomousEvidenceGapPlan.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise AutonomousEvidencePlanError(
            f"Autonomous evidence-gap plan is unreadable for run_id={run_id}."
        ) from exc
    return {
        **plan.model_dump(mode="json"),
        **autonomous_evidence_plan_summary_fields(plan),
        "autonomous_evidence_plan_path": path.relative_to(root_path).as_posix(),
    }


def render_autonomous_evidence_gap_plan_markdown(
    plan: AutonomousEvidenceGapPlan,
) -> str:
    """Render a concise reviewer/developer-facing autonomous plan."""
    fields = autonomous_evidence_plan_summary_fields(plan)
    lines = [
        "# Autonomous Evidence-Gap Plan",
        "",
        f"Run ID: `{plan.run_id}`",
        f"Planner backend: `{plan.planner_backend}`",
        f"Planner status: `{plan.planner_status}`",
        f"Plan items: `{fields['autonomous_plan_item_count']}`",
        f"Automation-ready items: `{fields['automation_ready_item_count']}`",
        (
            "Human intervention required: "
            f"`{str(plan.requires_human_intervention).lower()}`"
        ),
    ]
    if plan.human_intervention_reason_optional:
        lines.append(f"Human intervention reason: {plan.human_intervention_reason_optional}")
    lines.extend(["", "## Next Actions"])
    lines.extend(f"- {action}" for action in plan.next_action_summary or ["none"])
    lines.extend(["", "## Plan Items"])
    for item in plan.plan_items:
        lines.extend(
            [
                f"- `{item.item_id}`: `{item.gap_type}`",
                f"  - target: `{item.target_type}`",
                f"  - claim: `{item.target_claim_id_optional or 'none'}`",
                f"  - section: `{item.target_section_optional or 'none'}`",
                f"  - support: `{item.current_support_status}`",
                f"  - priority: `{item.priority}`",
                f"  - blocking: `{str(item.blocking).lower()}`",
                f"  - automation_ready: `{str(item.automation_ready).lower()}`",
                (
                    "  - gap exhausted: "
                    f"`{str(item.gap_exhausted).lower()}`"
                ),
                f"  - expected artifact: `{item.expected_artifact_type}`",
                f"  - rationale: {item.rationale}",
            ]
        )
    lines.extend(
        [
            "",
            "## Non-Evidence Flags",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def autonomous_evidence_plan_summary_fields(
    plan: AutonomousEvidenceGapPlan | None,
) -> dict[str, Any]:
    """Return stable inspect/lint summary fields for autonomous planning."""
    if plan is None:
        return {
            "autonomous_evidence_plan_present": False,
            "autonomous_plan_item_count": 0,
            "autonomous_python_experiment_item_count": 0,
            "autonomous_formal_proof_item_count": 0,
            "autonomous_retrieval_expansion_item_count": 0,
            "autonomous_claim_downgrade_item_count": 0,
            "autonomous_claim_removal_item_count": 0,
            "autonomous_manuscript_refresh_item_count": 0,
            "empirical_demonstration_gap_count": 0,
            "needs_python_experiment_count": 0,
            "bounded_empirical_claim_count": 0,
            "automation_ready_item_count": 0,
            "gap_attempt_history_present": False,
            "gap_attempt_count": 0,
            "gap_exhausted_no_progress_count": 0,
            "autonomous_human_intervention_required": False,
            "autonomous_next_actions": [],
        }
    counts = _gap_counts(plan.plan_items)
    return {
        "autonomous_evidence_plan_present": True,
        "autonomous_plan_item_count": len(plan.plan_items),
        "autonomous_python_experiment_item_count": counts["needs_python_experiment"],
        "autonomous_formal_proof_item_count": counts["needs_formal_proof"],
        "autonomous_retrieval_expansion_item_count": counts[
            "needs_retrieval_expansion"
        ],
        "autonomous_claim_downgrade_item_count": counts["needs_claim_downgrade"],
        "autonomous_claim_removal_item_count": counts["needs_claim_removal"],
        "autonomous_manuscript_refresh_item_count": counts[
            "needs_manuscript_refresh"
        ],
        "empirical_demonstration_gap_count": plan.empirical_demonstration_gap_count,
        "needs_python_experiment_count": plan.needs_python_experiment_count,
        "bounded_empirical_claim_count": plan.bounded_empirical_claim_count,
        "automation_ready_item_count": sum(
            1 for item in plan.plan_items if item.automation_ready
        ),
        "gap_attempt_history_present": plan.gap_attempt_history_present,
        "gap_attempt_count": plan.gap_attempt_count,
        "gap_exhausted_no_progress_count": plan.exhausted_gap_count,
        "autonomous_human_intervention_required": plan.requires_human_intervention,
        "autonomous_next_actions": list(plan.next_action_summary),
    }


def latest_autonomous_evidence_gap_plan_path(root: Path, run_id: str) -> Path | None:
    """Return the latest persisted autonomous evidence-gap plan JSON path."""
    reports = root / "runs" / run_id / "reports"
    if not reports.is_dir():
        return None
    paths = [
        path
        for path in reports.glob("autonomous-evidence-gap-plan*.json")
        if not path.name.endswith(".meta.json")
    ]
    if not paths:
        return None
    return sorted(paths, key=_plan_sort_key)[-1]


def _plan_items_for_claim_map(
    claim_map: ClaimEvidenceMap,
) -> list[AutonomousEvidenceGapPlanItem]:
    return [
        _plan_item_for_link(index=index, link=link)
        for index, link in enumerate(claim_map.links, start=1)
    ]


def _plan_item_for_link(
    *,
    index: int,
    link: ClaimEvidenceMapLink,
) -> AutonomousEvidenceGapPlanItem:
    del index
    if link.support_status in _SUPPORTED_STATUSES:
        return _supported_item(link)
    if _is_forbidden_authority_claim(link):
        return _claim_removal_item(link)
    if link.support_status == "partially_supported":
        return _partial_support_item(link)
    if _is_proof_or_theorem_claim(link):
        return _proof_item(link)
    if _is_empirical_or_result_claim(link):
        return _python_experiment_item(link)
    if _is_literature_or_background_claim(link):
        return _retrieval_item(link, blocking=True)
    return _claim_downgrade_item(link)


def _partial_support_item(link: ClaimEvidenceMapLink) -> AutonomousEvidenceGapPlanItem:
    if (
        link.support_type == "informal_proof_context"
        or _is_proof_or_theorem_claim(link)
    ):
        return _proof_item(
            link,
            rationale=(
                "The claim has only informal proof context; autonomous progression "
                "requires a scoped formal proof attempt or a downgrade."
            ),
            blocking=False,
        )
    if _is_empirical_or_result_claim(link):
        return _python_experiment_item(
            link,
            rationale=(
                "The claim has partial experiment context only; schedule a bounded "
                "Python experiment or downgrade the claim."
            ),
            blocking=False,
        )
    if _is_literature_or_background_claim(link):
        return _retrieval_item(
            link,
            blocking=False,
            rationale=(
                "The claim has partial source context only; expand retrieval or "
                "downgrade to bounded background wording."
            ),
        )
    return _claim_downgrade_item(link, blocking=False)


def _supported_item(link: ClaimEvidenceMapLink) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="sufficiently_supported_for_bounded_draft",
        recommended_action=(
            "Keep the claim as bounded draft wording; no automatic evidence action "
            "is required for this claim."
        ),
        priority="low",
        blocking=False,
        rationale=(
            f"Claim support is `{link.support_status}` with support type "
            f"`{link.support_type}` within the current evidence policy."
        ),
        required_inputs=[],
        expected_artifact_type="none",
        automation_ready=False,
    )


def _proof_item(
    link: ClaimEvidenceMapLink,
    *,
    rationale: str | None = None,
    blocking: bool = True,
) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="needs_formal_proof",
        recommended_action=(
            "Schedule a scoped formal proof attempt for the mapped theorem/proof "
            "claim, or downgrade the claim if proof is out of scope."
        ),
        priority="high" if blocking else "medium",
        blocking=blocking,
        rationale=rationale
        or (
            "The claim is proof/theorem-like and lacks a passed formal proof "
            "artifact linked to this claim ID or statement hash."
        ),
        required_inputs=[
            f"claim_id={link.claim_id}",
            f"claim_text_hash={link.claim_text_hash}",
            "formal proof backend/tooling",
        ],
        expected_artifact_type="proof_artifact",
        automation_ready=True,
    )


def _python_experiment_item(
    link: ClaimEvidenceMapLink,
    *,
    rationale: str | None = None,
    blocking: bool = True,
) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="needs_python_experiment",
        recommended_action=(
            "Schedule a bounded Python experiment for the mapped empirical/result "
            "claim, or downgrade the claim if no experiment should be run."
        ),
        priority="high" if blocking else "medium",
        blocking=blocking,
        rationale=rationale
        or (
            "The claim is empirical/result-like and lacks a completed experiment "
            "artifact with bounded metrics and result summary."
        ),
        required_inputs=[
            f"claim_id={link.claim_id}",
            f"section={link.section_name}",
            "experiment contract",
            "local Python runner configuration",
        ],
        expected_artifact_type="experiment_artifact",
        automation_ready=True,
    )


def _retrieval_item(
    link: ClaimEvidenceMapLink,
    *,
    blocking: bool,
    rationale: str | None = None,
) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="needs_retrieval_expansion",
        recommended_action=(
            "Expand bounded retrieval for the claim topic and rebuild the accepted "
            "citation registry, or downgrade the claim to source-boundary wording."
        ),
        priority="medium" if blocking else "low",
        blocking=blocking,
        rationale=rationale
        or (
            "The claim is literature/background/source-context oriented and lacks "
            "compatible accepted registry support."
        ),
        required_inputs=[
            f"claim_id={link.claim_id}",
            f"section={link.section_name}",
            "retrieval query or domain",
            "source quality filters",
        ],
        expected_artifact_type="retrieval_quality_report",
        automation_ready=True,
    )


def _claim_downgrade_item(
    link: ClaimEvidenceMapLink,
    *,
    blocking: bool = True,
) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="needs_claim_downgrade",
        recommended_action=(
            "Downgrade the unsupported claim to scaffold or evidence-boundary "
            "wording without inventing citations or evidence."
        ),
        priority="high" if blocking else "medium",
        blocking=blocking,
        rationale=(
            "The claim requires support but no compatible automatic evidence path "
            "is currently classified."
        ),
        required_inputs=[
            f"claim_id={link.claim_id}",
            "current manuscript",
            "claim-support audit",
        ],
        expected_artifact_type="revised_manuscript",
        automation_ready=True,
    )


def _claim_removal_item(link: ClaimEvidenceMapLink) -> AutonomousEvidenceGapPlanItem:
    return _item(
        link=link,
        gap_type="needs_claim_removal",
        recommended_action=(
            "Remove the forbidden novelty, validation, correctness, or publication "
            "readiness claim, or rewrite it as an evidence-boundary statement."
        ),
        priority="blocking",
        blocking=True,
        rationale=(
            "Planner policy does not allow proof, experiment, citation, or human "
            "review artifacts to support forbidden authority claims."
        ),
        required_inputs=[
            f"claim_id={link.claim_id}",
            "current manuscript",
            "claim-support audit",
        ],
        expected_artifact_type="revised_manuscript",
        automation_ready=True,
    )


def _bundle_level_plan_items(
    *,
    claim_map: ClaimEvidenceMap,
    retrieval_quality: RetrievalQualityReport | None,
    evidence_aware_refresh_present: bool,
    first_index: int,
) -> list[AutonomousEvidenceGapPlanItem]:
    items: list[AutonomousEvidenceGapPlanItem] = []
    if _needs_nonblocking_retrieval_expansion(claim_map, retrieval_quality):
        items.append(
            AutonomousEvidenceGapPlanItem(
                item_id=f"plan-item-{first_index:03d}",
                target_type="retrieval",
                current_support_status=(
                    retrieval_quality.adequacy_status
                    if retrieval_quality is not None
                    else "not_evaluated"
                ),
                gap_type="needs_retrieval_expansion",
                recommended_action=(
                    "Optionally expand bounded retrieval to improve background "
                    "coverage while preserving source-quality filtering."
                ),
                priority="low",
                blocking=False,
                rationale=(
                    "Retrieval adequacy remains bounded background context, but no "
                    "unsupported literature claim currently blocks the draft."
                ),
                required_inputs=[
                    "domain query",
                    "retrieval backend",
                    "source relevance and quality filters",
                ],
                expected_artifact_type="retrieval_quality_report",
                automation_ready=True,
            )
        )
    supported = claim_evidence_summary_fields(claim_map)
    if (
        not evidence_aware_refresh_present
        and not claim_map.unsupported_non_scaffold_claim_ids
        and (
            int(supported["proof_supported_claim_count"])
            or int(supported["experiment_supported_claim_count"])
        )
    ):
        items.append(
            AutonomousEvidenceGapPlanItem(
                item_id=f"plan-item-{first_index + len(items):03d}",
                target_type="manuscript",
                current_support_status="evidence_aware_refresh_missing",
                gap_type="needs_manuscript_refresh",
                recommended_action=(
                    "Refresh manuscript wording so linked proof or experiment "
                    "artifacts are described with bounded scope."
                ),
                priority="medium",
                blocking=False,
                rationale=(
                    "The claim-evidence map has proof or experiment support, but "
                    "the evidence-aware refresh report is absent."
                ),
                required_inputs=[
                    "claim-evidence map",
                    "current manuscript",
                    "citation registry",
                ],
                expected_artifact_type="evidence_aware_refresh_report",
                automation_ready=True,
            )
        )
    return items


def _item(
    *,
    link: ClaimEvidenceMapLink,
    gap_type: str,
    recommended_action: str,
    priority: str,
    blocking: bool,
    rationale: str,
    required_inputs: list[str],
    expected_artifact_type: str,
    automation_ready: bool,
) -> AutonomousEvidenceGapPlanItem:
    return AutonomousEvidenceGapPlanItem(
        item_id="plan-item-000",
        target_type="claim",
        target_claim_id_optional=link.claim_id,
        target_section_optional=link.section_name,
        current_support_status=link.support_status,
        gap_type=gap_type,
        recommended_action=recommended_action,
        priority=priority,
        blocking=blocking,
        rationale=rationale,
        required_inputs=required_inputs,
        expected_artifact_type=expected_artifact_type,
        automation_ready=automation_ready,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _needs_nonblocking_retrieval_expansion(
    claim_map: ClaimEvidenceMap,
    retrieval_quality: RetrievalQualityReport | None,
) -> bool:
    if retrieval_quality is None:
        return False
    has_blocking_literature_gap = any(
        link.requires_support
        and link.support_status in {"unsupported", "partially_supported"}
        and _is_literature_or_background_claim(link)
        for link in claim_map.links
    )
    if has_blocking_literature_gap:
        return False
    return bool(
        retrieval_quality.adequacy_status
        in {"bounded_context_only", "insufficient_sources"}
        or retrieval_quality.rejected_source_count > 0
    )


def _is_forbidden_authority_claim(link: ClaimEvidenceMapLink) -> bool:
    text = _link_text(link)
    return link.support_status == "blocked_forbidden_claim" or any(
        phrase in text
        for phrase in (
            "novelty",
            "publication",
            "readiness",
            "validation",
            "correctness",
            "validated",
        )
    )


def _is_proof_or_theorem_claim(link: ClaimEvidenceMapLink) -> bool:
    text = _link_text(link)
    return any(phrase in text for phrase in ("proof", "theorem", "lemma", "formal"))


def _is_empirical_or_result_claim(link: ClaimEvidenceMapLink) -> bool:
    if link.claim_class in BOUNDED_EMPIRICAL_CLAIM_CLASSES:
        return True
    text = _link_text(link)
    return any(
        phrase in text
        for phrase in ("experiment", "empirical", "result", "metric", "demonstration")
    )


def _is_literature_or_background_claim(link: ClaimEvidenceMapLink) -> bool:
    text = _link_text(link)
    return any(
        phrase in text
        for phrase in (
            "literature",
            "background",
            "source_context",
            "external_factual",
            "source",
            "citation",
        )
    )


def _link_text(link: ClaimEvidenceMapLink) -> str:
    return " ".join(
        [
            link.claim_class,
            link.support_type,
            link.support_scope,
            link.unsupported_reason or "",
            link.classification,
        ]
    ).casefold()


def _claim_map_contradiction(claim_map: ClaimEvidenceMap) -> str | None:
    computed = _computed_summary_counts(claim_map.links)
    for key, value in computed.items():
        if int(claim_map.summary_counts.get(key, value)) != value:
            return (
                "Claim-evidence map summary counts do not match the contained links; "
                "manual inspection is required before autonomous planning."
            )
    expected = sorted(
        link.claim_id
        for link in claim_map.links
        if link.requires_support
        and link.support_status in {"unsupported", "blocked_forbidden_claim"}
    )
    if sorted(claim_map.unsupported_non_scaffold_claim_ids) != expected:
        return (
            "Claim-evidence map unsupported claim IDs do not match contained links; "
            "manual inspection is required before autonomous planning."
        )
    return None


def _computed_summary_counts(links: list[ClaimEvidenceMapLink]) -> dict[str, int]:
    counts = {
        "total_claim_count": len(links),
        "supported_within_scope": 0,
        "partially_supported": 0,
        "unsupported": 0,
        "not_required_scaffold": 0,
        "blocked_forbidden_claim": 0,
        "citation_supported_background_claim": 0,
        "proof_supported_claim": 0,
        "experiment_supported_claim": 0,
        "human_reviewed_claim": 0,
        "scaffold_or_boundary_statement": 0,
        "unsupported_claim": 0,
    }
    for link in links:
        counts[link.support_status] += 1
        counts[link.classification] += 1
    counts["unsupported_non_scaffold_count"] = sum(
        1
        for link in links
        if link.requires_support
        and link.support_status in {"unsupported", "blocked_forbidden_claim"}
    )
    return counts


def _blocked_plan(
    *,
    run_id: str,
    backend: str,
    status: str,
    reason: str,
    claim_map_path: str | None = None,
    claim_support_path: str | None = None,
    retrieval_quality_path: str | None = None,
) -> AutonomousEvidenceGapPlan:
    return _finalize_plan(
        run_id=run_id,
        backend=backend,
        status=status,
        claim_map_path=claim_map_path,
        claim_support_path=claim_support_path,
        retrieval_quality_path=retrieval_quality_path,
        items=[],
        requires_human_intervention=True,
        human_reason=reason,
    )


def _finalize_plan(
    *,
    run_id: str,
    backend: str,
    status: str,
    claim_map_path: str | None,
    claim_support_path: str | None,
    retrieval_quality_path: str | None,
    items: list[AutonomousEvidenceGapPlanItem],
    requires_human_intervention: bool,
    human_reason: str | None,
) -> AutonomousEvidenceGapPlan:
    next_actions = _next_action_summary(items, human_reason)
    history_present = any(item.gap_attempt_history_present for item in items)
    gap_attempt_count = sum(item.gap_attempt_count for item in items)
    exhausted_gap_count = sum(item.gap_exhausted for item in items)
    empirical_demonstration_gap_count = sum(
        1
        for item in items
        if item.gap_type == "needs_python_experiment"
        and item.target_claim_id_optional == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
    )
    bounded_empirical_claim_count = sum(
        1
        for item in items
        if item.target_claim_id_optional == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
    )
    return AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend=backend,
        planner_status=status,
        claim_evidence_map_path=claim_map_path,
        claim_support_audit_path=claim_support_path,
        retrieval_quality_report_path=retrieval_quality_path,
        plan_items=items,
        next_action_summary=next_actions,
        ready_for_python_experiment_runner=_has_ready_gap(
            items,
            "needs_python_experiment",
        ),
        ready_for_formal_proof_attempt=_has_ready_gap(items, "needs_formal_proof"),
        ready_for_retrieval_expansion=_has_ready_gap(
            items,
            "needs_retrieval_expansion",
        ),
        ready_for_manuscript_refresh=_has_ready_gap(
            items,
            "needs_manuscript_refresh",
        ),
        gap_attempt_history_present=history_present,
        gap_attempt_count=gap_attempt_count,
        exhausted_gap_count=exhausted_gap_count,
        empirical_demonstration_gap_count=empirical_demonstration_gap_count,
        needs_python_experiment_count=sum(
            1 for item in items if item.gap_type == "needs_python_experiment"
        ),
        bounded_empirical_claim_count=bounded_empirical_claim_count,
        requires_human_intervention=requires_human_intervention,
        human_intervention_reason_optional=human_reason,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _next_action_summary(
    items: list[AutonomousEvidenceGapPlanItem],
    human_reason: str | None,
) -> list[str]:
    if human_reason:
        return [human_reason]
    actions = []
    for item in items:
        if item.automation_ready and item.gap_type != "sufficiently_supported_for_bounded_draft":
            actions.append(item.recommended_action)
    if not actions:
        return [
            "All mapped claims are sufficiently supported for a bounded draft; no "
            "automatic evidence-producing action is required."
        ]
    return sorted(set(actions))


def _has_ready_gap(
    items: list[AutonomousEvidenceGapPlanItem],
    gap_type: str,
) -> bool:
    return any(item.gap_type == gap_type and item.automation_ready for item in items)


def _gap_counts(items: list[AutonomousEvidenceGapPlanItem]) -> dict[str, int]:
    keys = {
        "needs_python_experiment": 0,
        "needs_formal_proof": 0,
        "needs_retrieval_expansion": 0,
        "needs_claim_downgrade": 0,
        "needs_claim_removal": 0,
        "needs_manuscript_refresh": 0,
        "sufficiently_supported_for_bounded_draft": 0,
    }
    for item in items:
        keys[item.gap_type] += 1
    return keys


def _path_if_exists(path: Path, root: Path) -> str | None:
    return path.relative_to(root).as_posix() if path.is_file() else None


def _read_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _next_plan_id(root: Path, run_id: str) -> str:
    reports = root / "runs" / run_id / "reports"
    if not (reports / "autonomous-evidence-gap-plan.json").exists():
        return "autonomous-evidence-gap-plan"
    existing = [
        path
        for path in reports.glob("autonomous-evidence-gap-plan-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return f"autonomous-evidence-gap-plan-{len(existing) + 2:04d}"


def _next_plan_reviewer_summary_id(root: Path, run_id: str) -> str:
    existing = [
        path
        for path in (root / "runs" / run_id / "reports").glob(
            "reviewer-bundle-summary-after-autonomous-evidence-plan-*.json"
        )
        if not path.name.endswith(".meta.json")
    ]
    return f"reviewer-bundle-summary-after-autonomous-evidence-plan-{len(existing) + 1:04d}"


def _plan_sort_key(path: Path) -> tuple[int, str]:
    if path.name == "autonomous-evidence-gap-plan.json":
        return (0, path.name)
    match = re.match(r"autonomous-evidence-gap-plan-(\d+)\.json$", path.name)
    if match:
        return (int(match.group(1)), path.name)
    return (-1, path.name)


__all__ = [
    "AutonomousEvidencePlanError",
    "AutonomousEvidenceGapPlanPersistResult",
    "autonomous_evidence_plan_summary_fields",
    "build_autonomous_evidence_gap_plan",
    "inspect_autonomous_evidence_gap_plan",
    "latest_autonomous_evidence_gap_plan_path",
    "persist_autonomous_evidence_gap_plan",
    "render_autonomous_evidence_gap_plan_markdown",
]
