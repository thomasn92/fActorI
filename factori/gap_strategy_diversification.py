"""Deterministic attempt-aware strategy diversification for exhausted gaps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.gap_attempts import latest_gap_attempt_history_path
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    ControllerActionType,
    GapAttemptHistory,
    GapAttemptRecord,
    GapStrategyDiversificationIndex,
    GapStrategyDiversificationReport,
    GapStrategyOption,
)

_BACKENDS = {"deterministic", "fake", "openai"}
_CANDIDATE_STATUSES = {
    "exhausted_no_progress",
    "exhausted_initial_strategy",
    "exhausted_all_strategies",
}
_SPEC_STRATEGY_FAMILIES = {
    "retrieval_query_variant",
    "retrieval_source_type_variant",
    "proof_decomposition_variant",
    "proof_checker_variant",
    "experiment_metric_variant",
    "experiment_baseline_variant",
    "experiment_dataset_variant",
}


class GapStrategyDiversificationError(RuntimeError):
    """Raised when deterministic gap diversification cannot proceed safely."""


@dataclass(frozen=True)
class GapStrategyDiversificationResult:
    """Persisted strategy diversification report and immutable latest index."""

    report: GapStrategyDiversificationReport
    index: GapStrategyDiversificationIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


def strategy_fingerprint(
    *,
    gap_fingerprint: str,
    target_claim_id_optional: str | None,
    target_section_optional: str | None,
    gap_type: str,
    alternative_action: str,
    strategy_family: str,
    expected_artifact_type: str,
    required_inputs: list[str],
) -> str:
    """Return a stable fingerprint for one alternative strategy."""
    return sha256_json(
        {
            "kind": "gap_strategy",
            "gap_fingerprint": gap_fingerprint,
            "target_claim_id_optional": target_claim_id_optional,
            "target_section_optional": target_section_optional,
            "gap_type": gap_type,
            "alternative_action": alternative_action,
            "strategy_family": strategy_family,
            "expected_artifact_type": expected_artifact_type,
            "required_inputs": sorted(required_inputs),
        }
    )


def strategy_is_automation_ready(option: GapStrategyOption) -> bool:
    """Return whether an unattempted option is executable by current local adapters."""
    text = " ".join(
        [option.alternative_action, option.rationale, *option.required_inputs]
    ).casefold()
    forbidden = (
        "over the network",
        "requires network",
        "network_required",
        "external api",
        "arbitrary python",
        "invoke lean",
        "external proof tool",
    )
    return option.novel_relative_to_previous_attempts and not any(
        marker in text for marker in forbidden
    )


def build_gap_strategy_diversification(
    *,
    run_id: str,
    root: str | Path = ".",
    backend: str = "deterministic",
) -> GapStrategyDiversificationReport:
    """Build the next bounded alternative-strategy report without persistence."""
    if backend not in _BACKENDS:
        raise GapStrategyDiversificationError(
            "strategy backend must be deterministic, fake, or openai"
        )
    if backend == "openai":
        raise GapStrategyDiversificationError(
            "OpenAI strategy diversification is schema-gated but not implemented in M71."
        )
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    history_path = latest_gap_attempt_history_path(root_path, run_id)
    plan_path = _latest_plan_path(reports)
    if history_path is None or plan_path is None:
        raise GapStrategyDiversificationError(
            "Gap-attempt history and an autonomous plan are required for diversification."
        )
    try:
        history = GapAttemptHistory.model_validate_json(
            history_path.read_text(encoding="utf-8")
        )
        plan = AutonomousEvidenceGapPlan.model_validate_json(
            plan_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise GapStrategyDiversificationError(
            "Gap-attempt history or autonomous plan is corrupt."
        ) from exc
    if history.run_id != run_id or plan.run_id != run_id:
        raise GapStrategyDiversificationError("Diversification input run_id does not match.")

    number = _next_diversification_number(reports)
    seen = _previous_strategy_fingerprints(reports, run_id)
    candidates = [
        record
        for record in history.records
        if record.current_gap_status in _CANDIDATE_STATUSES
        and record.gap_type
        in {
            "needs_python_experiment",
            "needs_formal_proof",
            "needs_retrieval_expansion",
            "needs_claim_downgrade",
            "needs_claim_removal",
        }
    ]
    options: list[GapStrategyOption] = []
    selected_ids: list[str] = []
    for record in candidates:
        record_options = _options_for_record(record, seen, len(options) + 1)
        selected = next((item for item in record_options if item.automation_ready), None)
        if selected is not None:
            selected_ids.append(selected.strategy_id)
            record_options = [
                item.model_copy(update={"selected": item.strategy_id == selected.strategy_id})
                for item in record_options
            ]
        options.extend(record_options)
    selected = [item for item in options if item.selected]
    new_count = sum(item.novel_relative_to_previous_attempts for item in options)
    status = (
        "no_candidate_gaps"
        if not candidates
        else "diversified"
        if selected
        else "all_strategies_exhausted"
    )
    return GapStrategyDiversificationReport(
        run_id=run_id,
        diversification_id=f"gap-strategy-diversification-{number:04d}",
        source_gap_attempt_history_path=history_path.relative_to(root_path).as_posix(),
        source_autonomous_plan_path=plan_path.relative_to(root_path).as_posix(),
        strategy_backend=backend,
        strategy_status=status,
        candidate_gap_count=len(candidates),
        strategy_option_count=len(options),
        new_strategy_count=new_count,
        duplicate_strategy_count=len(options) - new_count,
        selected_strategy_count=len(selected),
        selected_strategy_ids=selected_ids,
        created_plan_item_count=len(selected),
        created_spec_count=0,
        strategy_options=options,
        requires_human_intervention=False,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def persist_gap_strategy_diversification(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    backend: str = "deterministic",
) -> GapStrategyDiversificationResult:
    """Persist one append-only diversification report, Markdown view, and index."""
    root_path = Path(root)
    report = build_gap_strategy_diversification(
        run_id=run_id,
        root=root_path,
        backend=backend,
    )
    number = int(report.diversification_id.rsplit("-", maxsplit=1)[-1])
    index_id = f"gap-strategy-diversification-index-{number:04d}"
    _previous_report, previous_index = latest_gap_strategy_diversification(
        root_path,
        run_id,
    )
    index = GapStrategyDiversificationIndex(
        run_id=run_id,
        latest_diversification_id=report.diversification_id,
        diversification_count=number,
        latest_strategy_status=report.strategy_status,
        latest_strategy_option_count=report.strategy_option_count,
        latest_selected_strategy_count=report.selected_strategy_count,
        latest_duplicate_strategy_count=report.duplicate_strategy_count,
        strategy_option_count=(
            (previous_index.strategy_option_count if previous_index else 0)
            + report.strategy_option_count
        ),
        selected_strategy_count=(
            (previous_index.selected_strategy_count if previous_index else 0)
            + report.selected_strategy_count
        ),
        duplicate_strategy_count=(
            (previous_index.duplicate_strategy_count if previous_index else 0)
            + report.duplicate_strategy_count
        ),
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                report.diversification_id,
                ArtifactType.REPORT,
                report,
                "json",
                _metadata("gap_strategy_diversification_context"),
            ),
            ArtifactWriteSpec(
                f"{report.diversification_id}-markdown",
                ArtifactType.REPORT,
                render_gap_strategy_diversification_markdown(report),
                "markdown",
                _metadata("gap_strategy_diversification_context"),
                filename_stem=report.diversification_id,
            ),
            ArtifactWriteSpec(
                index_id,
                ArtifactType.REPORT,
                index,
                "json",
                _metadata("gap_strategy_diversification_index_context"),
            ),
        ],
        action_type=ControllerActionType.GAP_STRATEGY_DIVERSIFICATION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "diversification_id": report.diversification_id,
            "strategy_status": report.strategy_status,
            "strategy_option_count": report.strategy_option_count,
            "selected_strategy_count": report.selected_strategy_count,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return GapStrategyDiversificationResult(
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report.diversification_id],
        report_markdown_artifact=by_id[f"{report.diversification_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def selected_strategy_plan_items(
    *,
    root: str | Path,
    run_id: str,
    history: GapAttemptHistory | None,
) -> list[AutonomousEvidenceGapPlanItem]:
    """Convert latest selected unattempted options into executable plan items."""
    report, _index = latest_gap_strategy_diversification(Path(root), run_id)
    if report is None:
        return []
    attempted = {
        fingerprint
        for record in (history.records if history else [])
        for fingerprint in record.strategy_fingerprints_attempted
    }
    result: list[AutonomousEvidenceGapPlanItem] = []
    for option in report.strategy_options:
        if not option.selected or option.strategy_fingerprint in attempted:
            continue
        gap_type = _plan_gap_type(option)
        result.append(
            AutonomousEvidenceGapPlanItem(
                item_id=f"strategy-{option.strategy_id}",
                target_type="claim" if option.target_claim_id_optional else "bundle",
                target_claim_id_optional=option.target_claim_id_optional,
                target_section_optional=option.target_section_optional,
                current_support_status="exhausted_strategy_diversification",
                gap_type=gap_type,
                recommended_action=option.alternative_action,
                priority="high",
                blocking=False,
                rationale=option.rationale,
                required_inputs=[
                    *option.required_inputs,
                    f"strategy_family={option.strategy_family}",
                    f"strategy_fingerprint={option.strategy_fingerprint}",
                ],
                expected_artifact_type=option.expected_artifact_type,
                automation_ready=option.automation_ready,
                source_gap_fingerprint=option.gap_fingerprint,
                strategy_fingerprint=option.strategy_fingerprint,
                strategy_family=option.strategy_family,
            )
        )
    return result


def latest_gap_strategy_diversification(
    root: Path,
    run_id: str,
) -> tuple[GapStrategyDiversificationReport | None, GapStrategyDiversificationIndex | None]:
    """Load the latest valid diversification report and index."""
    reports = root / "runs" / run_id / "reports"
    indexes = sorted(
        path
        for path in reports.glob("gap-strategy-diversification-index-*.json")
        if not path.name.endswith(".meta.json")
    )
    if not indexes:
        return None, None
    try:
        index = GapStrategyDiversificationIndex.model_validate_json(
            indexes[-1].read_text(encoding="utf-8")
        )
        report = GapStrategyDiversificationReport.model_validate_json(
            (reports / f"{index.latest_diversification_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def inspect_gap_strategy_diversification(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect the latest diversification report without mutation."""
    root_path = Path(root)
    report, index = latest_gap_strategy_diversification(root_path, run_id)
    if report is None or index is None:
        raise GapStrategyDiversificationError(
            f"No gap strategy diversification found for run_id={run_id}."
        )
    return {
        **report.model_dump(mode="json"),
        **strategy_diversification_summary_fields(report, index),
        "gap_strategy_diversification_report_path": (
            f"runs/{run_id}/reports/{report.diversification_id}.json"
        ),
    }


def strategy_diversification_summary_fields(
    report: GapStrategyDiversificationReport | None,
    index: GapStrategyDiversificationIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint summary fields for diversification."""
    if report is None:
        return {
            "strategy_diversification_present": False,
            "strategy_diversification_count": 0,
            "strategy_option_count": 0,
            "selected_strategy_count": 0,
            "duplicate_strategy_count": 0,
            "latest_strategy_option_count": 0,
            "latest_selected_strategy_count": 0,
            "latest_duplicate_strategy_count": 0,
            "gaps_deferred_after_strategy_exhaustion": 0,
        }
    deferred = len(
        {
            option.gap_fingerprint
            for option in report.strategy_options
            if report.strategy_status == "all_strategies_exhausted"
        }
    )
    return {
        "strategy_diversification_present": True,
        "strategy_diversification_count": index.diversification_count if index else 1,
        "strategy_option_count": (
            index.strategy_option_count if index else report.strategy_option_count
        ),
        "selected_strategy_count": (
            index.selected_strategy_count if index else report.selected_strategy_count
        ),
        "duplicate_strategy_count": (
            index.duplicate_strategy_count if index else report.duplicate_strategy_count
        ),
        "latest_strategy_option_count": report.strategy_option_count,
        "latest_selected_strategy_count": report.selected_strategy_count,
        "latest_duplicate_strategy_count": report.duplicate_strategy_count,
        "gaps_deferred_after_strategy_exhaustion": deferred,
    }


def render_gap_strategy_diversification_markdown(
    report: GapStrategyDiversificationReport,
) -> str:
    """Render a concise reviewer-facing diversification report."""
    lines = [
        "# Gap Strategy Diversification",
        "",
        f"Run ID: `{report.run_id}`",
        f"Diversification ID: `{report.diversification_id}`",
        f"Status: `{report.strategy_status}`",
        f"Candidate gaps: `{report.candidate_gap_count}`",
        f"Strategy options: `{report.strategy_option_count}`",
        f"Selected strategies: `{report.selected_strategy_count}`",
        f"Duplicate strategies: `{report.duplicate_strategy_count}`",
        "",
        "## Options",
    ]
    for option in report.strategy_options:
        lines.append(
            f"- `{option.strategy_id}` `{option.strategy_family}`: "
            f"selected=`{str(option.selected).lower()}`, "
            f"automation_ready=`{str(option.automation_ready).lower()}`"
        )
    lines.extend(
        [
            "",
            "## Non-Evidence Boundary",
            "- Diversification schedules bounded workflow alternatives only.",
            "- It creates no proof, experiment evidence, validation, or publication readiness.",
            "- publication_ready: false",
            "",
        ]
    )
    return "\n".join(lines)


def _options_for_record(
    record: GapAttemptRecord,
    seen: set[str],
    first_index: int,
) -> list[GapStrategyOption]:
    templates = _strategy_templates(record)
    options: list[GapStrategyOption] = []
    for offset, (family, action, expected, required, rationale) in enumerate(templates):
        fingerprint = strategy_fingerprint(
            gap_fingerprint=record.gap_fingerprint,
            target_claim_id_optional=record.target_claim_id_optional,
            target_section_optional=record.target_section_optional,
            gap_type=record.gap_type,
            alternative_action=action,
            strategy_family=family,
            expected_artifact_type=expected,
            required_inputs=required,
        )
        novel = fingerprint not in seen
        option = GapStrategyOption(
            strategy_id=f"strategy-{first_index + offset:04d}",
            gap_fingerprint=record.gap_fingerprint,
            target_claim_id_optional=record.target_claim_id_optional,
            target_section_optional=record.target_section_optional,
            gap_type=record.gap_type,
            original_recommended_action=record.recommended_action,
            alternative_action=action,
            strategy_family=family,
            strategy_fingerprint=fingerprint,
            novel_relative_to_previous_attempts=novel,
            expected_artifact_type=expected,
            required_inputs=required,
            automation_ready=False,
            rationale=rationale,
            safety_notes=[
                "Local deterministic workflow only; no network or external tool is allowed.",
                "The strategy is not evidence and cannot imply publication readiness.",
            ],
        )
        options.append(
            option.model_copy(update={"automation_ready": strategy_is_automation_ready(option)})
        )
    return options


def _strategy_templates(
    record: GapAttemptRecord,
) -> list[tuple[str, str, str, list[str], str]]:
    target = record.target_claim_id_optional or record.target_section_optional or "bounded context"
    if record.gap_type == "needs_retrieval_expansion":
        return [
            (
                "retrieval_query_variant",
                f"Narrow local retrieval query terms to {target} and its section vocabulary.",
                "retrieval_quality_report",
                ["local_fixture_only", "narrow_query_terms", f"target={target}"],
                "A narrower local query can differ materially without network access.",
            ),
            (
                "retrieval_source_type_variant",
                "Search an OpenAlex-style local source pack before applying existing hard filters.",
                "retrieval_quality_report",
                ["local_source_pack", "openalex_style_fixture", "existing_hard_filters"],
                "A different local source-pack shape may provide bounded context safely.",
            ),
        ]
    if record.gap_type == "needs_formal_proof":
        return [
            (
                "proof_decomposition_variant",
                f"Split {target} into deterministic subclaims and create a proof-plan obligation.",
                "proof_artifact",
                ["proof_plan_only", "decompose_statement", f"target={target}"],
                "Decomposition changes the proof planning strategy without asserting verification.",
            ),
            (
                "proof_checker_variant",
                f"Create a scoped checker-neutral certificate obligation for {target}.",
                "proof_artifact",
                ["local_contract_only", "checker_neutral_certificate", f"target={target}"],
                "A checker-neutral contract is materially different but remains unverified.",
            ),
            (
                "claim_downgrade_variant",
                f"Downgrade {target} from theorem language to an explicit proof obligation.",
                "manuscript_revision",
                ["boundary_language_only", f"target={target}"],
                "Downgrading avoids an unsupported theorem claim when formal proof is unavailable.",
            ),
        ]
    if record.gap_type == "needs_python_experiment":
        return [
            (
                "experiment_metric_variant",
                f"Plan a synthetic local ablation for {target} using a robustness metric.",
                "experiment_artifact",
                ["built_in_template_only", "robustness_metric", f"target={target}"],
                "A different bounded metric is safe for the built-in deterministic adapter.",
            ),
            (
                "experiment_baseline_variant",
                f"Plan a synthetic local comparison for {target} with a null baseline.",
                "experiment_artifact",
                ["built_in_template_only", "null_baseline", f"target={target}"],
                "A distinct baseline changes the bounded experiment strategy.",
            ),
            (
                "experiment_dataset_variant",
                f"Plan a fixed-seed synthetic calibration dataset for {target}.",
                "experiment_artifact",
                ["built_in_template_only", "synthetic_calibration", f"target={target}"],
                "A different built-in synthetic dataset remains local and bounded.",
            ),
            (
                "claim_downgrade_variant",
                f"Downgrade {target} to a bounded demonstration statement pending real evidence.",
                "manuscript_revision",
                ["boundary_language_only", f"target={target}"],
                "Downgrading prevents broad empirical authority without a completed artifact.",
            ),
        ]
    if record.gap_type in {"needs_claim_removal", "needs_claim_downgrade"}:
        return [
            (
                "claim_removal_variant",
                f"Remove unsupported authority language associated with {target}.",
                "manuscript_revision",
                ["deterministic_text_revision", f"target={target}"],
                "Removal is safe when bounded wording cannot preserve the claim.",
            ),
            (
                "manuscript_boundary_variant",
                f"Move {target} to explicit future-work and evidence-boundary language.",
                "manuscript_revision",
                ["boundary_language_only", f"target={target}"],
                "Future-work wording preserves the unresolved gap without asserting support.",
            ),
        ]
    return []


def _plan_gap_type(option: GapStrategyOption) -> str:
    if option.strategy_family == "claim_downgrade_variant":
        return "needs_claim_downgrade"
    if option.strategy_family == "claim_removal_variant":
        return "needs_claim_removal"
    if option.strategy_family == "manuscript_boundary_variant":
        return "needs_manuscript_refresh"
    return option.gap_type


def _previous_strategy_fingerprints(reports: Path, run_id: str) -> set[str]:
    result: set[str] = set()
    for path in sorted(reports.glob("gap-strategy-diversification-*.json")):
        if "index" in path.name or path.name.endswith(".meta.json"):
            continue
        try:
            report = GapStrategyDiversificationReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if report.run_id == run_id:
            result.update(
                option.strategy_fingerprint
                for option in report.strategy_options
                if option.selected
            )
    return result


def _latest_plan_path(reports: Path) -> Path | None:
    paths = [
        path
        for path in reports.glob("autonomous-evidence-gap-plan*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths, key=_plan_sort_key)[-1] if paths else None


def _plan_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"-(\d+)\.json$", path.name)
    return (int(match.group(1)) if match else 0, path.name)


def _next_diversification_number(reports: Path) -> int:
    numbers = []
    for path in reports.glob("gap-strategy-diversification-*.json"):
        match = re.fullmatch(r"gap-strategy-diversification-(\d+)\.json", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "gap_strategy_diversification",
        "artifact_role": role,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "publication_ready": False,
    }


__all__ = [
    "GapStrategyDiversificationError",
    "GapStrategyDiversificationResult",
    "build_gap_strategy_diversification",
    "inspect_gap_strategy_diversification",
    "latest_gap_strategy_diversification",
    "persist_gap_strategy_diversification",
    "render_gap_strategy_diversification_markdown",
    "selected_strategy_plan_items",
    "strategy_diversification_summary_fields",
    "strategy_fingerprint",
    "strategy_is_automation_ready",
]
