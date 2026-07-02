"""Deterministic local/offline capability escalation for deferred gaps."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import persist_autonomous_evidence_gap_plan
from factori.citations import build_citation_registry
from factori.claim_evidence import persist_claim_evidence_map
from factori.gap_attempts import latest_gap_attempt_history_path
from factori.hashing import canonical_json, sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.retrieval import (
    _load_local_source_records,
    _local_source_result,
    score_retrieval_sources,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousLoopGapTerminalClassification,
    AutonomousLoopIndex,
    AutonomousLoopRunReport,
    CapabilityEscalationIndex,
    CapabilityEscalationItem,
    CapabilityEscalationPolicy,
    CapabilityEscalationReport,
    ControllerActionType,
    FullPaperReleaseGateConfig,
    GapAttemptHistory,
    ProofArtifact,
)

_PROOF_GAP_TYPES = {"needs_formal_proof"}
_RETRIEVAL_GAP_TYPES = {"needs_retrieval_expansion"}
_PROOF_TERMINAL_CLASSES = {"deferred_exhausted_proof", "deferred_requires_external_tool"}
_RETRIEVAL_TERMINAL_CLASSES = {"deferred_exhausted_retrieval", "deferred_requires_network"}
_TERMINAL_REPORT_CLASSES = _PROOF_TERMINAL_CLASSES | _RETRIEVAL_TERMINAL_CLASSES
_REPO_ROOT = Path(__file__).resolve().parent.parent


class CapabilityEscalationError(RuntimeError):
    """Raised when capability escalation cannot proceed safely."""


@dataclass(frozen=True)
class CapabilityEscalationResult:
    """Persisted escalation report and latest index."""

    run_id: str
    report: CapabilityEscalationReport
    index: CapabilityEscalationIndex
    policy: CapabilityEscalationPolicy
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef
    policy_artifact: ArtifactRef


def escalate_capabilities(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    allow_network: bool = False,
    allow_external_proof_tools: bool = False,
    allow_external_retrieval_tools: bool = False,
    allowed_proof_backends: list[str] | None = None,
    allowed_retrieval_backends: list[str] | None = None,
    max_escalation_attempts_per_gap: int = 1,
    max_escalation_attempts_per_loop: int = 4,
    max_retrieval_sources_per_escalation: int = 8,
    max_tool_runtime_seconds: int = 30,
) -> CapabilityEscalationResult:
    """Attempt allowed local/offline escalation for deferred proof/retrieval gaps."""
    if min(
        max_escalation_attempts_per_gap,
        max_escalation_attempts_per_loop,
        max_retrieval_sources_per_escalation,
        max_tool_runtime_seconds,
    ) < 0:
        raise CapabilityEscalationError("Capability escalation budgets must be non-negative.")
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not reports.is_dir():
        raise CapabilityEscalationError(f"No run reports directory found for run_id={run_id}.")

    number = _next_escalation_number(reports)
    escalation_id = f"capability-escalation-{number:04d}"
    policy = CapabilityEscalationPolicy(
        run_id=run_id,
        allow_network=allow_network,
        allow_external_proof_tools=allow_external_proof_tools,
        allow_external_retrieval_tools=allow_external_retrieval_tools,
        allowed_proof_backends=allowed_proof_backends
        or [
            "proof_plan_refinement_local",
            "formal_proof_fixture_local",
            "lean_external_disabled",
        ],
        allowed_retrieval_backends=allowed_retrieval_backends
        or [
            "local_source_pack_expansion",
            "bibliography_fixture_expansion",
            "network_retrieval_disabled",
        ],
        max_escalation_attempts_per_gap=max_escalation_attempts_per_gap,
        max_escalation_attempts_per_loop=max_escalation_attempts_per_loop,
        max_retrieval_sources_per_escalation=max_retrieval_sources_per_escalation,
        max_tool_runtime_seconds=max_tool_runtime_seconds,
        fail_closed=True,
        publication_ready=False,
    )
    loop_report, loop_report_path = _latest_loop_report(root_path, run_id)
    history, history_path = _latest_history(root_path, run_id)
    candidates = _deferred_candidates(loop_report, history)
    items: list[CapabilityEscalationItem] = []
    extra_specs: list[ArtifactWriteSpec] = []
    created_paths: list[str] = []
    ingested_paths: list[str] = []
    attempts = 0
    for candidate in candidates:
        if attempts >= policy.max_escalation_attempts_per_loop:
            items.append(_budget_deferred_item(candidate, len(items) + 1))
            continue
        if _attempts_for_gap(items, candidate.gap_fingerprint) >= (
            policy.max_escalation_attempts_per_gap
        ):
            items.append(_budget_deferred_item(candidate, len(items) + 1))
            continue
        if candidate.gap_type in _PROOF_GAP_TYPES:
            item, proof_path = _escalate_proof_gap(
                candidate=candidate,
                item_index=len(items) + 1,
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                policy=policy,
                escalation_id=escalation_id,
            )
            items.append(item)
            attempts += int(item.backend_allowed_by_policy)
            if proof_path:
                created_paths.append(proof_path)
                ingested_paths.append(proof_path)
        elif candidate.gap_type in _RETRIEVAL_GAP_TYPES:
            item, specs = _escalate_retrieval_gap(
                candidate=candidate,
                item_index=len(items) + 1,
                run_id=run_id,
                root=root_path,
                policy=policy,
                escalation_id=escalation_id,
            )
            items.append(item)
            attempts += int(item.backend_allowed_by_policy)
            extra_specs.extend(specs)
            if item.created_artifact_path_optional:
                created_paths.append(item.created_artifact_path_optional)
        else:
            items.append(_unsupported_candidate_item(candidate, len(items) + 1))

    map_rebuilt = False
    plan_rebuilt = False
    release_report = None
    if items:
        try:
            map_result = persist_claim_evidence_map(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
            )
            created_paths.append(map_result.map_artifact.path)
            map_rebuilt = True
            plan_result = persist_autonomous_evidence_gap_plan(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend="deterministic",
            )
            created_paths.append(plan_result.plan_artifact.path)
            plan_rebuilt = True
            from factori.full_paper_release import evaluate_full_paper_release  # noqa: PLC0415

            release_report = evaluate_full_paper_release(
                run_id=run_id,
                root=root_path,
                ledger=ledger,
                config=FullPaperReleaseGateConfig(
                    run_id=run_id,
                    allow_warnings=True,
                    require_latex_export=False,
                    require_citations=False,
                    require_revision_status=False,
                    write_report=False,
                ),
            )
            release_rechecked = True
        except Exception as exc:  # pragma: no cover - defensive fail-closed path
            raise CapabilityEscalationError(
                "Post-escalation rebuild failed; escalation fails closed."
            ) from exc
    else:
        release_rechecked = False

    successful = sum(
        item.execution_status
        in {"created_context_artifact", "created_formal_artifact", "expanded_local_sources"}
        for item in items
    )
    created_paths.extend(_artifact_paths_for_specs(run_id, extra_specs))
    failed = sum(
        item.execution_status in {"failed", "rejected_policy"} for item in items
    )
    deferred = sum(
        item.execution_status in {"deferred", "no_match_found", "created_context_artifact"}
        for item in items
        if item.gap_type in _PROOF_GAP_TYPES | _RETRIEVAL_GAP_TYPES
    )
    status = (
        "no_candidate_deferred_gaps"
        if not candidates
        else "completed"
        if items and deferred == 0 and failed == 0
        else "completed_with_deferred_gaps"
    )
    policy_path = f"runs/{run_id}/reports/capability-escalation-policy-{number:04d}.json"
    report = CapabilityEscalationReport(
        run_id=run_id,
        escalation_id=escalation_id,
        policy_path=policy_path,
        source_loop_report_path=(
            loop_report_path.relative_to(root_path).as_posix() if loop_report_path else None
        ),
        source_gap_attempt_history_path=(
            history_path.relative_to(root_path).as_posix() if history_path else None
        ),
        escalation_status=status,
        network_allowed=policy.allow_network,
        external_tools_allowed=(
            policy.allow_external_proof_tools or policy.allow_external_retrieval_tools
        ),
        candidate_deferred_gap_count=len(candidates),
        attempted_gap_count=sum(item.backend_allowed_by_policy for item in items),
        proof_escalation_attempt_count=sum(
            item.gap_type in _PROOF_GAP_TYPES and item.backend_allowed_by_policy
            for item in items
        ),
        retrieval_escalation_attempt_count=sum(
            item.gap_type in _RETRIEVAL_GAP_TYPES and item.backend_allowed_by_policy
            for item in items
        ),
        successful_escalation_count=successful,
        failed_escalation_count=failed,
        deferred_after_escalation_count=deferred,
        created_artifact_paths=sorted(set(created_paths)),
        ingested_artifact_paths=sorted(set(ingested_paths)),
        claim_evidence_map_rebuilt=map_rebuilt,
        autonomous_plan_rebuilt=plan_rebuilt,
        release_rechecked=release_rechecked,
        items=items,
        requires_human_intervention=False,
        publication_ready=False,
    )
    return _persist_escalation(
        report=report,
        policy=policy,
        root=root_path,
        store=store,
        ledger=ledger,
        escalation_number=number,
        extra_specs=extra_specs,
        release_report=release_report,
    )


def inspect_capability_escalation(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect latest capability escalation without mutation."""
    root_path = Path(root)
    report, index = latest_capability_escalation_report(root_path, run_id)
    if report is None or index is None:
        raise CapabilityEscalationError(f"No capability escalation found for run_id={run_id}.")
    report_path = root_path / "runs" / run_id / "reports" / f"{report.escalation_id}.json"
    index_path = latest_capability_escalation_index_path(root_path, run_id)
    return {
        **report.model_dump(mode="json"),
        **capability_escalation_summary_fields(report, index),
        "capability_escalation_report_path": report_path.relative_to(root_path).as_posix(),
        "capability_escalation_index_path": (
            index_path.relative_to(root_path).as_posix() if index_path else None
        ),
        "capability_escalation_index": index.model_dump(mode="json"),
    }


def latest_capability_escalation_index_path(root: Path, run_id: str) -> Path | None:
    reports = root / "runs" / run_id / "reports"
    paths = [
        path
        for path in reports.glob("capability-escalation-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def latest_capability_escalation_report(
    root: Path,
    run_id: str,
) -> tuple[CapabilityEscalationReport | None, CapabilityEscalationIndex | None]:
    index_path = latest_capability_escalation_index_path(root, run_id)
    if index_path is None:
        return None, None
    try:
        index = CapabilityEscalationIndex.model_validate_json(
            index_path.read_text(encoding="utf-8")
        )
        report = CapabilityEscalationReport.model_validate_json(
            (
                root
                / "runs"
                / run_id
                / "reports"
                / f"{index.latest_escalation_id}.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def capability_escalation_summary_fields(
    report: CapabilityEscalationReport | None,
    index: CapabilityEscalationIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint fields for capability escalation."""
    if report is None:
        return {
            "capability_escalation_present": False,
            "capability_escalation_status": None,
            "capability_escalation_count": 0,
            "proof_escalation_attempt_count": 0,
            "retrieval_escalation_attempt_count": 0,
            "successful_escalation_count": 0,
            "deferred_after_escalation_count": 0,
            "capability_escalation_network_allowed": False,
            "capability_escalation_external_tools_allowed": False,
            "capability_escalation_requires_human_intervention": False,
        }
    return {
        "capability_escalation_present": True,
        "capability_escalation_status": report.escalation_status,
        "capability_escalation_count": index.escalation_count if index else 1,
        "proof_escalation_attempt_count": report.proof_escalation_attempt_count,
        "retrieval_escalation_attempt_count": report.retrieval_escalation_attempt_count,
        "successful_escalation_count": report.successful_escalation_count,
        "deferred_after_escalation_count": report.deferred_after_escalation_count,
        "capability_escalation_network_allowed": report.network_allowed,
        "capability_escalation_external_tools_allowed": report.external_tools_allowed,
        "capability_escalation_requires_human_intervention": (
            report.requires_human_intervention
        ),
    }


def render_capability_escalation_markdown(report: CapabilityEscalationReport) -> str:
    """Render a concise reviewer-facing capability escalation report."""
    lines = [
        "# Capability Escalation Report",
        "",
        f"Run ID: `{report.run_id}`",
        f"Escalation ID: `{report.escalation_id}`",
        f"Status: `{report.escalation_status}`",
        f"Candidate deferred gaps: `{report.candidate_deferred_gap_count}`",
        f"Attempted gaps: `{report.attempted_gap_count}`",
        f"Proof escalations attempted: `{report.proof_escalation_attempt_count}`",
        f"Retrieval escalations attempted: `{report.retrieval_escalation_attempt_count}`",
        f"Successful escalations: `{report.successful_escalation_count}`",
        f"Failed escalations: `{report.failed_escalation_count}`",
        f"Deferred after escalation: `{report.deferred_after_escalation_count}`",
        f"Claim-evidence map rebuilt: `{str(report.claim_evidence_map_rebuilt).lower()}`",
        f"Autonomous plan rebuilt: `{str(report.autonomous_plan_rebuilt).lower()}`",
        f"Release rechecked: `{str(report.release_rechecked).lower()}`",
        f"Publication ready: `{str(report.publication_ready).lower()}`",
        "",
        "## Items",
    ]
    if not report.items:
        lines.append("- none")
    for item in report.items:
        lines.append(
            f"- `{item.item_id}` `{item.gap_type}` via `{item.selected_backend}`: "
            f"`{item.execution_status}`"
        )
        if item.failure_reason_optional:
            lines.append(f"  - reason: {item.failure_reason_optional}")
    lines.extend(
        [
            "",
            "## Non-Evidence Boundary",
            "- Capability escalation is workflow automation only.",
            "- Proof plans are not formal verification.",
            "- Retrieval expansion is bounded background context only.",
            "- Network and external tools are disabled unless a future explicit policy "
            "enables them.",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class _DeferredGapCandidate:
    gap_fingerprint: str | None
    target_claim_id_optional: str | None
    target_section_optional: str | None
    gap_type: str
    deferred_reason: str


def _deferred_candidates(
    loop_report: AutonomousLoopRunReport | None,
    history: GapAttemptHistory | None,
) -> list[_DeferredGapCandidate]:
    by_key: dict[tuple[str | None, str], _DeferredGapCandidate] = {}
    for item in (loop_report.gap_terminal_classifications if loop_report else []):
        if not _terminal_class_is_candidate(item):
            continue
        candidate = _DeferredGapCandidate(
            gap_fingerprint=item.gap_fingerprint,
            target_claim_id_optional=item.target_claim_id_optional,
            target_section_optional=item.target_section_optional,
            gap_type=item.gap_type,
            deferred_reason=item.reason,
        )
        by_key[(candidate.gap_fingerprint, candidate.gap_type)] = candidate
    for record in (history.records if history else []):
        if record.gap_type not in _PROOF_GAP_TYPES | _RETRIEVAL_GAP_TYPES:
            continue
        if record.current_gap_status not in {
            "deferred",
            "exhausted_no_progress",
            "exhausted_initial_strategy",
            "exhausted_all_strategies",
            "deferred_after_diversification",
        }:
            continue
        by_key.setdefault(
            (record.gap_fingerprint, record.gap_type),
            _DeferredGapCandidate(
                gap_fingerprint=record.gap_fingerprint,
                target_claim_id_optional=record.target_claim_id_optional,
                target_section_optional=record.target_section_optional,
                gap_type=record.gap_type,
                deferred_reason=(
                    record.exhaustion_reason_optional
                    or record.resolution_reason_optional
                    or "Gap is deferred after local attempts."
                ),
            ),
        )
    return sorted(
        by_key.values(),
        key=lambda item: (
            item.gap_type,
            item.target_claim_id_optional or "",
            item.gap_fingerprint or "",
        ),
    )


def _terminal_class_is_candidate(item: AutonomousLoopGapTerminalClassification) -> bool:
    return (
        item.terminal_class in _TERMINAL_REPORT_CLASSES
        and item.gap_type in _PROOF_GAP_TYPES | _RETRIEVAL_GAP_TYPES
    )


def _escalate_proof_gap(
    *,
    candidate: _DeferredGapCandidate,
    item_index: int,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    policy: CapabilityEscalationPolicy,
    escalation_id: str,
) -> tuple[CapabilityEscalationItem, str | None]:
    if "proof_plan_refinement_local" not in policy.allowed_proof_backends:
        return _policy_rejected_item(
            candidate,
            item_index,
            selected_backend="lean_external_disabled",
            reason="External proof tools are disabled and no local proof-plan backend is allowed.",
        ), None
    proof = _proof_plan_artifact(
        candidate=candidate,
        run_id=run_id,
        escalation_id=escalation_id,
    )
    formal = _formal_fixture_artifact(
        candidate=candidate,
        run_id=run_id,
        root=root,
        escalation_id=escalation_id,
    )
    if formal is not None and "formal_proof_fixture_local" in policy.allowed_proof_backends:
        proof = formal
        selected_backend = "formal_proof_fixture_local"
        status = "created_formal_artifact"
        notes = [
            "Fixture-backed formal proof is accepted only because checker status is "
            "passed and the target identifier matches exactly.",
            "The artifact remains scoped to the mapped claim and does not imply "
            "novelty, broad correctness, or publication readiness.",
        ]
    else:
        selected_backend = "proof_plan_refinement_local"
        status = "created_context_artifact"
        notes = [
            "Local proof-plan refinement creates proof context only.",
            "It does not count as formal verification and leaves the formal proof gap deferred.",
            "Lean/external proof escalation remains disabled by policy.",
        ]
    from factori.evidence_artifact_intake import EvidenceArtifactIntakeError  # noqa: PLC0415

    try:
        proof_path = _ingest_generated_proof(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            proof=proof,
        )
    except EvidenceArtifactIntakeError as exc:
        return (
            CapabilityEscalationItem(
                item_id=f"capability-escalation-item-{item_index:03d}",
                gap_fingerprint=candidate.gap_fingerprint,
                target_claim_id_optional=candidate.target_claim_id_optional,
                target_section_optional=candidate.target_section_optional,
                gap_type=candidate.gap_type,
                deferred_reason=candidate.deferred_reason,
                selected_backend=selected_backend,
                backend_allowed_by_policy=True,
                execution_status="failed",
                failure_reason_optional=str(exc),
                safety_notes=[
                    "Generated proof artifact failed existing proof-artifact intake validation."
                ],
                publication_ready=False,
            ),
            None,
        )
    return (
        CapabilityEscalationItem(
            item_id=f"capability-escalation-item-{item_index:03d}",
            gap_fingerprint=candidate.gap_fingerprint,
            target_claim_id_optional=candidate.target_claim_id_optional,
            target_section_optional=candidate.target_section_optional,
            gap_type=candidate.gap_type,
            deferred_reason=candidate.deferred_reason,
            selected_backend=selected_backend,
            backend_allowed_by_policy=True,
            execution_status=status,
            ingested_artifact_path_optional=proof_path,
            safety_notes=notes,
            is_verification_evidence=proof.is_verification_evidence,
            publication_ready=False,
        ),
        proof_path,
    )


def _escalate_retrieval_gap(
    *,
    candidate: _DeferredGapCandidate,
    item_index: int,
    run_id: str,
    root: Path,
    policy: CapabilityEscalationPolicy,
    escalation_id: str,
) -> tuple[CapabilityEscalationItem, list[ArtifactWriteSpec]]:
    if "local_source_pack_expansion" not in policy.allowed_retrieval_backends:
        return _policy_rejected_item(
            candidate,
            item_index,
            selected_backend="network_retrieval_disabled",
            reason="Network and external retrieval tools are disabled by policy.",
        ), []
    source_pack = _local_source_pack_path(root)
    if source_pack is None:
        return (
            CapabilityEscalationItem(
                item_id=f"capability-escalation-item-{item_index:03d}",
                gap_fingerprint=candidate.gap_fingerprint,
                target_claim_id_optional=candidate.target_claim_id_optional,
                target_section_optional=candidate.target_section_optional,
                gap_type=candidate.gap_type,
                deferred_reason=candidate.deferred_reason,
                selected_backend="local_source_pack_expansion",
                backend_allowed_by_policy=True,
                execution_status="no_match_found",
                failure_reason_optional="No local source-pack fixture was available.",
                safety_notes=[
                    "Network retrieval is disabled; deferred retrieval gap remains visible."
                ],
                publication_ready=False,
            ),
            [],
        )
    query = _query_for_candidate(candidate)
    records = _load_local_source_records(source_pack)[: policy.max_retrieval_sources_per_escalation]
    results = [
        _local_source_result(record, query=query, rank=index)
        for index, record in enumerate(records)
    ]
    scored, quality = score_retrieval_sources(
        run_id=run_id,
        retrieval_backend="local_source_pack_expansion",
        query=query,
        results=results,
        domain=query,
        candidate_title_or_problem=query,
    )
    registry = build_citation_registry(run_id, scored)
    item_id = f"capability-escalation-item-{item_index:03d}"
    quality_id = f"{escalation_id}-retrieval-quality-{item_index:03d}"
    registry_id = f"{escalation_id}-retrieval-citation-registry-{item_index:03d}"
    quality_path = f"runs/{run_id}/reports/{quality_id}.json"
    accepted_count = quality.accepted_source_count
    status = "expanded_local_sources" if accepted_count else "no_match_found"
    notes = [
        "Local source-pack expansion used deterministic retrieval quality filtering.",
        "Rejected and hard-rejected sources remain excluded from the escalation citation registry.",
        "Accepted sources are bounded background context only, not validation.",
        "Network retrieval remains disabled by policy.",
    ]
    return (
        CapabilityEscalationItem(
            item_id=item_id,
            gap_fingerprint=candidate.gap_fingerprint,
            target_claim_id_optional=candidate.target_claim_id_optional,
            target_section_optional=candidate.target_section_optional,
            gap_type=candidate.gap_type,
            deferred_reason=candidate.deferred_reason,
            selected_backend="local_source_pack_expansion",
            backend_allowed_by_policy=True,
            execution_status=status,
            created_artifact_path_optional=quality_path,
            failure_reason_optional=(
                None if accepted_count else "No accepted local expansion source found."
            ),
            safety_notes=notes,
            publication_ready=False,
        ),
        [
            ArtifactWriteSpec(
                quality_id,
                ArtifactType.REPORT,
                quality,
                "json",
                _metadata("retrieval_expansion_quality_context"),
            ),
            ArtifactWriteSpec(
                registry_id,
                ArtifactType.REPORT,
                registry,
                "json",
                _metadata("retrieval_expansion_citation_registry_context"),
            ),
        ],
    )


def _proof_plan_artifact(
    *,
    candidate: _DeferredGapCandidate,
    run_id: str,
    escalation_id: str,
) -> ProofArtifact:
    target = candidate.target_claim_id_optional or candidate.gap_fingerprint or "unmapped-proof-gap"
    statement = (
        "Refined local proof plan for a deferred mapped claim. "
        "This artifact records decomposition context only and is not a checked proof."
    )
    proof_hash = sha256_json(
        {
            "run_id": run_id,
            "target": target,
            "statement": statement,
            "gap": candidate.gap_fingerprint,
        }
    )
    timestamp = "1970-01-01T00:00:00Z"
    return ProofArtifact(
        run_id=run_id,
        proof_id=f"{escalation_id}-proof-plan-{_safe_suffix(target)}",
        proof_type="proof_plan",
        claim_ids_or_statement_ids=[target],
        statement=statement,
        checker_name_optional="proof_plan_refinement_local",
        checker_version_optional="m76",
        checker_status="not_checked",
        proof_hash=proof_hash,
        review_status="local_proof_plan_context_only",
        limitations=[
            "This proof-plan artifact is not formal verification.",
            "It does not establish novelty, correctness validation, or publication readiness.",
            "A passed formal checker artifact is still required for formal proof support.",
        ],
        created_at=timestamp,
        ingested_at=timestamp,
        is_verification_evidence=False,
    )


def _formal_fixture_artifact(
    *,
    candidate: _DeferredGapCandidate,
    run_id: str,
    root: Path,
    escalation_id: str,
) -> ProofArtifact | None:
    fixture = root / "tests" / "fixtures" / "proof" / "escalation_formal_fixture_passed.json"
    if not fixture.is_file():
        return None
    try:
        proof = ProofArtifact.model_validate_json(fixture.read_text(encoding="utf-8"))
    except ValueError:
        return None
    target = candidate.target_claim_id_optional or candidate.gap_fingerprint
    if not target or target not in proof.claim_ids_or_statement_ids:
        return None
    if proof.proof_type not in {"lean_verified", "formal_verified", "external_certificate"}:
        return None
    if proof.checker_status != "passed" or not proof.is_verification_evidence:
        return None
    timestamp = "1970-01-01T00:00:00Z"
    return proof.model_copy(
        update={
            "run_id": run_id,
            "proof_id": f"{escalation_id}-formal-fixture-{_safe_suffix(target)}",
            "created_at": timestamp,
            "ingested_at": timestamp,
        }
    )


def _ingest_generated_proof(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    proof: ProofArtifact,
) -> str:
    from factori.evidence_artifact_intake import ingest_proof_artifact  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="factori-capability-proof-") as directory:
        path = Path(directory) / "proof.json"
        path.write_text(canonical_json(proof) + "\n", encoding="utf-8")
        result = ingest_proof_artifact(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            proof_file=path,
        )
    return result.proof_artifact.path


def _policy_rejected_item(
    candidate: _DeferredGapCandidate,
    item_index: int,
    *,
    selected_backend: str,
    reason: str,
) -> CapabilityEscalationItem:
    return CapabilityEscalationItem(
        item_id=f"capability-escalation-item-{item_index:03d}",
        gap_fingerprint=candidate.gap_fingerprint,
        target_claim_id_optional=candidate.target_claim_id_optional,
        target_section_optional=candidate.target_section_optional,
        gap_type=candidate.gap_type,
        deferred_reason=candidate.deferred_reason,
        selected_backend=selected_backend,
        backend_allowed_by_policy=False,
        execution_status="rejected_policy",
        failure_reason_optional=reason,
        safety_notes=[
            "Capability escalation failed closed because the requested backend is disallowed."
        ],
        publication_ready=False,
    )


def _budget_deferred_item(
    candidate: _DeferredGapCandidate,
    item_index: int,
) -> CapabilityEscalationItem:
    return CapabilityEscalationItem(
        item_id=f"capability-escalation-item-{item_index:03d}",
        gap_fingerprint=candidate.gap_fingerprint,
        target_claim_id_optional=candidate.target_claim_id_optional,
        target_section_optional=candidate.target_section_optional,
        gap_type=candidate.gap_type,
        deferred_reason=candidate.deferred_reason,
        selected_backend="budget_guard",
        backend_allowed_by_policy=False,
        execution_status="deferred",
        failure_reason_optional="Capability escalation budget was exhausted.",
        safety_notes=["Budget exhaustion is recorded and the deferred gap remains visible."],
        publication_ready=False,
    )


def _unsupported_candidate_item(
    candidate: _DeferredGapCandidate,
    item_index: int,
) -> CapabilityEscalationItem:
    return CapabilityEscalationItem(
        item_id=f"capability-escalation-item-{item_index:03d}",
        gap_fingerprint=candidate.gap_fingerprint,
        target_claim_id_optional=candidate.target_claim_id_optional,
        target_section_optional=candidate.target_section_optional,
        gap_type=candidate.gap_type,
        deferred_reason=candidate.deferred_reason,
        selected_backend="none",
        backend_allowed_by_policy=False,
        execution_status="deferred",
        failure_reason_optional="No safe proof or retrieval escalation backend applies.",
        safety_notes=["Unsupported deferred gap remains visible."],
        publication_ready=False,
    )


def _attempts_for_gap(items: list[CapabilityEscalationItem], fingerprint: str | None) -> int:
    return sum(
        item.gap_fingerprint == fingerprint and item.backend_allowed_by_policy
        for item in items
    )


def _local_source_pack_path(root: Path) -> Path | None:
    candidates = [
        root / "tests" / "fixtures" / "retrieval" / "local_source_pack_expansion.json",
        _REPO_ROOT
        / "tests"
        / "fixtures"
        / "retrieval"
        / "local_source_pack_expansion.json",
        root / "tests" / "fixtures" / "retrieval" / "openalex_style_human_geography_sources.json",
        _REPO_ROOT
        / "tests"
        / "fixtures"
        / "retrieval"
        / "openalex_style_human_geography_sources.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _query_for_candidate(candidate: _DeferredGapCandidate) -> str:
    terms = [
        candidate.target_section_optional or "",
        candidate.target_claim_id_optional or "",
        candidate.deferred_reason,
        "human geography bounded background context",
    ]
    return " ".join(term for term in terms if term).strip()


def _persist_escalation(
    *,
    report: CapabilityEscalationReport,
    policy: CapabilityEscalationPolicy,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    escalation_number: int,
    extra_specs: list[ArtifactWriteSpec],
    release_report,
) -> CapabilityEscalationResult:
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    report_id = report.escalation_id
    policy_id = f"capability-escalation-policy-{escalation_number:04d}"
    index_id = f"capability-escalation-index-{escalation_number:04d}"
    reviewer_id = f"reviewer-bundle-summary-after-capability-escalation-{escalation_number:04d}"
    release_id = f"full-paper-release-report-after-capability-escalation-{escalation_number:04d}"
    final_paths = set(report.created_artifact_paths)
    final_paths.update(
        {
            f"runs/{report.run_id}/reports/{policy_id}.json",
            f"runs/{report.run_id}/reports/{report_id}.json",
            f"runs/{report.run_id}/reports/{report_id}.md",
            f"runs/{report.run_id}/reports/{index_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.md",
        }
    )
    if release_report is not None:
        final_paths.update(
            {
                f"runs/{report.run_id}/reports/{release_id}.json",
                f"runs/{report.run_id}/reports/{release_id}.md",
            }
        )
    report = report.model_copy(update={"created_artifact_paths": sorted(final_paths)})
    _, previous_index = latest_capability_escalation_report(root, report.run_id)
    index = CapabilityEscalationIndex(
        run_id=report.run_id,
        latest_escalation_id=report.escalation_id,
        escalation_count=escalation_number,
        latest_escalation_status=report.escalation_status,
        proof_escalation_attempt_count=(
            (previous_index.proof_escalation_attempt_count if previous_index else 0)
            + report.proof_escalation_attempt_count
        ),
        retrieval_escalation_attempt_count=(
            (previous_index.retrieval_escalation_attempt_count if previous_index else 0)
            + report.retrieval_escalation_attempt_count
        ),
        successful_escalation_count=(
            (previous_index.successful_escalation_count if previous_index else 0)
            + report.successful_escalation_count
        ),
        deferred_after_escalation_count=(
            (previous_index.deferred_after_escalation_count if previous_index else 0)
            + report.deferred_after_escalation_count
        ),
        latest_artifact_paths=report.created_artifact_paths,
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=report.run_id,
        root=root,
        release_report=release_report,
        capability_escalation_report=report,
    )
    specs = [
        ArtifactWriteSpec(
            policy_id,
            ArtifactType.REPORT,
            policy,
            "json",
            _metadata("capability_escalation_policy_context"),
        ),
        *extra_specs,
        ArtifactWriteSpec(
            report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("capability_escalation_context"),
        ),
        ArtifactWriteSpec(
            f"{report_id}-markdown",
            ArtifactType.REPORT,
            render_capability_escalation_markdown(report),
            "markdown",
            _metadata("capability_escalation_context"),
            filename_stem=report_id,
        ),
        ArtifactWriteSpec(
            index_id,
            ArtifactType.REPORT,
            index,
            "json",
            _metadata("capability_escalation_index_context"),
        ),
        ArtifactWriteSpec(
            reviewer_id,
            ArtifactType.REPORT,
            reviewer,
            "json",
            _metadata("reviewer_bundle_summary_context"),
        ),
        ArtifactWriteSpec(
            f"{reviewer_id}-markdown",
            ArtifactType.REPORT,
            render_reviewer_bundle_summary_markdown(reviewer),
            "markdown",
            _metadata("reviewer_bundle_summary_context"),
            filename_stem=reviewer_id,
        ),
    ]
    if release_report is not None:
        specs.extend(
            [
                ArtifactWriteSpec(
                    release_id,
                    ArtifactType.REPORT,
                    release_report,
                    "json",
                    _metadata("full_paper_release_audit_context"),
                ),
                ArtifactWriteSpec(
                    f"{release_id}-markdown",
                    ArtifactType.REPORT,
                    render_full_paper_release_summary(release_report),
                    "markdown",
                    _metadata("full_paper_release_audit_context"),
                    filename_stem=release_id,
                ),
            ]
        )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.CAPABILITY_ESCALATION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "escalation_id": report.escalation_id,
            "escalation_status": report.escalation_status,
            "proof_escalation_attempt_count": report.proof_escalation_attempt_count,
            "retrieval_escalation_attempt_count": report.retrieval_escalation_attempt_count,
            "successful_escalation_count": report.successful_escalation_count,
            "deferred_after_escalation_count": report.deferred_after_escalation_count,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return CapabilityEscalationResult(
        run_id=report.run_id,
        report=report,
        index=index,
        policy=policy,
        persistence=persistence,
        report_artifact=by_id[report_id],
        report_markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
        policy_artifact=by_id[policy_id],
    )


def _metadata(role: str) -> dict[str, object]:
    return {
        "stage": "capability_escalation",
        "role": role,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "publication_ready": False,
    }


def _artifact_paths_for_specs(run_id: str, specs: list[ArtifactWriteSpec]) -> list[str]:
    paths = []
    extension_by_format = {
        "json": "json",
        "markdown": "md",
        "latex": "tex",
        "bib": "bib",
        "text": (specs[0].extension if specs else "txt"),
    }
    for spec in specs:
        extension = (
            spec.extension.removeprefix(".")
            if spec.extension
            else extension_by_format.get(spec.artifact_format, spec.artifact_format)
        )
        stem = spec.filename_stem or spec.artifact_id
        directory = "reports" if spec.artifact_type == ArtifactType.REPORT else "artifacts"
        paths.append(f"runs/{run_id}/{directory}/{stem}.{extension}")
    return paths


def _latest_loop_report(
    root: Path,
    run_id: str,
) -> tuple[AutonomousLoopRunReport | None, Path | None]:
    reports = root / "runs" / run_id / "reports"
    index_paths = [
        path
        for path in reports.glob("autonomous-loop-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    if not index_paths:
        return None, None
    try:
        index = AutonomousLoopIndex.model_validate_json(
            sorted(index_paths)[-1].read_text(encoding="utf-8")
        )
        report_path = reports / f"{index.latest_loop_id}.json"
        report = AutonomousLoopRunReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, report_path


def _latest_history(
    root: Path,
    run_id: str,
) -> tuple[GapAttemptHistory | None, Path | None]:
    history_path = latest_gap_attempt_history_path(root, run_id)
    if history_path is None:
        return None, None
    try:
        history = GapAttemptHistory.model_validate_json(
            history_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, history_path
    return history, history_path


def _next_escalation_number(reports: Path) -> int:
    paths = [
        path
        for path in reports.glob("capability-escalation-*.json")
        if not path.name.startswith("capability-escalation-index-")
        and not path.name.startswith("capability-escalation-policy-")
        and not path.name.endswith(".meta.json")
    ]
    return len(paths) + 1


def _safe_suffix(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value).strip("-")[
        :80
    ] or "gap"
