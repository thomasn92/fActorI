"""Strict backend-authority inventory and production-mode enforcement."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ControllerActionType,
    ProductionModePolicy,
    ProductionModeReport,
    ProductionModeViolation,
    ScientificStageKind,
    StageBackendRecord,
)

_REPORT_RE = re.compile(r"^production-mode-report-(\d{4})\.json$")
_STAGE_FILES: tuple[tuple[re.Pattern[str], ScientificStageKind], ...] = (
    (
        re.compile(r"^domain-method-atlas-\d{4}\.json$"),
        ScientificStageKind.ATLAS_CONSTRUCTION,
    ),
    (re.compile(r"^atlas-scan-\d{4}\.json$"), ScientificStageKind.PAIR_RANKING),
    (
        re.compile(r"^deep-opportunity-discovery-report-\d{4}\.json$"),
        ScientificStageKind.OPPORTUNITY_DISCOVERY,
    ),
    (re.compile(r"^opportunity-discovery-\d{4}\.json$"), ScientificStageKind.OPPORTUNITY_DISCOVERY),
    (re.compile(r"^variance-augmentation-\d{4}\.json$"), ScientificStageKind.VARIANCE_GENERATION),
    (
        re.compile(r"^llm-variance-generation-report-\d{4}\.json$"),
        ScientificStageKind.VARIANCE_GENERATION,
    ),
    (
        re.compile(r"^idea-tree-construction-report-\d{4}\.json$"),
        ScientificStageKind.IDEA_TREE_CONSTRUCTION,
    ),
    (re.compile(r"^substrate-promotion-\d{4}\.json$"), ScientificStageKind.SUBSTRATE_CONSTRUCTION),
    (re.compile(r"^branch-route-plan-\d{4}\.json$"), ScientificStageKind.BRANCH_ROUTING),
    (
        re.compile(r"^route-execution-spec-build-\d{4}\.json$"),
        ScientificStageKind.EXPERIMENT_DESIGN,
    ),
    (re.compile(r"^route-execution-report-\d{4}\.json$"), ScientificStageKind.EXPERIMENT_EXECUTION),
)


class ProductionModeError(RuntimeError):
    """Raised when backend inventory or policy evaluation cannot be completed."""


@dataclass(frozen=True)
class ProductionModeCheckResult:
    """Persisted production-mode report."""

    run_id: str
    report: ProductionModeReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef


def stage_backend_record(
    *,
    stage_id: str,
    stage_kind: ScientificStageKind,
    backend_kind: BackendKind,
    backend_name: str,
    is_scientific_generation: bool,
    is_scientific_judgment: bool,
    is_execution_or_verification: bool,
    reason: str,
    artifact_ids: list[str],
    allowed_in_production: bool | None = None,
    fallback_used: bool = False,
    fallback_disclosed: bool = True,
) -> StageBackendRecord:
    """Construct an explicit backend record with conservative authority defaults."""
    allowed = (
        _default_production_allowed(
            stage_kind=stage_kind,
            backend_kind=backend_kind,
            is_scientific_generation=is_scientific_generation,
            is_scientific_judgment=is_scientific_judgment,
            is_execution_or_verification=is_execution_or_verification,
        )
        if allowed_in_production is None
        else allowed_in_production
    )
    return StageBackendRecord(
        stage_id=stage_id,
        stage_kind=stage_kind,
        backend_kind=backend_kind,
        backend_name=backend_name,
        is_scientific_generation=is_scientific_generation,
        is_scientific_judgment=is_scientific_judgment,
        is_execution_or_verification=is_execution_or_verification,
        allowed_in_production=allowed,
        reason=reason,
        artifact_ids=artifact_ids,
        fallback_used=fallback_used,
        fallback_disclosed=fallback_disclosed,
    )


def collect_backend_records(
    *, run_id: str, root: str | Path = "."
) -> tuple[list[StageBackendRecord], list[ScientificStageKind], list[str]]:
    """Collect explicit records and conservatively infer legacy M91-M95 artifacts."""
    reports = Path(root) / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise ProductionModeError(f"Reports directory not found for run_id={run_id}.")
    selected_files: list[tuple[Path, ScientificStageKind]] = []
    for pattern, stage_kind in _STAGE_FILES:
        matches = [path for path in reports.iterdir() if pattern.match(path.name)]
        if matches:
            selected_files.append((max(matches, key=lambda path: path.name), stage_kind))

    records: list[StageBackendRecord] = []
    detected_stages: list[ScientificStageKind] = []
    warnings: list[str] = []
    for path, stage_kind in selected_files:
        detected_stages.append(stage_kind)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductionModeError(
                f"Could not inspect backend metadata in {path}: {exc}"
            ) from exc
        explicit = payload.get("backend_records", [])
        if explicit:
            try:
                records.extend(StageBackendRecord.model_validate(item) for item in explicit)
            except ValidationError as exc:
                raise ProductionModeError(f"Invalid backend record in {path}: {exc}") from exc
        else:
            inferred = _infer_legacy_records(path=path, stage_kind=stage_kind, payload=payload)
            records.extend(inferred)
            warnings.append(f"Inferred backend classification for legacy artifact {path.name}.")
    return records, detected_stages, warnings


def evaluate_production_mode(
    *,
    run_id: str,
    records: list[StageBackendRecord],
    policy: ProductionModePolicy,
    expected_stage_kinds: list[ScientificStageKind] | None = None,
    report_id: str = "production-mode-derived",
    warnings: list[str] | None = None,
) -> ProductionModeReport:
    """Evaluate records under dev or strict non-fake policy without persistence."""
    violations: list[ProductionModeViolation] = []
    expected = list(dict.fromkeys(expected_stage_kinds or []))
    represented = {record.stage_kind for record in records}
    missing = [stage_kind for stage_kind in expected if stage_kind not in represented]
    strict = policy.require_non_fake_backends

    if strict and policy.fail_on_missing_backend_record and not records:
        violations.append(
            ProductionModeViolation(
                stage_kind=ScientificStageKind.OPPORTUNITY_DISCOVERY,
                artifact_id="missing:backend_inventory",
                backend_kind=BackendKind.FAKE,
                violation_type="missing_backend_inventory",
                message="No backend records were found for strict production-mode evaluation.",
                blocking=True,
            )
        )

    if strict and policy.fail_on_missing_backend_record:
        violations.extend(
            ProductionModeViolation(
                stage_kind=stage_kind,
                artifact_id=f"missing:{stage_kind.value}",
                backend_kind=BackendKind.FAKE,
                violation_type="missing_backend_record",
                message=f"No backend record exists for expected stage {stage_kind.value}.",
                blocking=True,
            )
            for stage_kind in missing
        )

    for record in records:
        artifact_id = record.artifact_ids[0] if record.artifact_ids else record.stage_id
        pair = f"{record.stage_kind.value}:{record.backend_kind.value}"
        pair_forbidden = pair in policy.forbidden_scientific_stage_backend_pairs
        policy_allowed = _record_allowed_by_policy(record=record, policy=policy)
        if strict and (not record.allowed_in_production or not policy_allowed or pair_forbidden):
            violations.append(
                ProductionModeViolation(
                    stage_kind=record.stage_kind,
                    artifact_id=artifact_id,
                    backend_kind=record.backend_kind,
                    violation_type="non_production_backend",
                    message=(
                        f"Stage {record.stage_kind.value} uses {record.backend_kind.value} "
                        f"backend {record.backend_name}, which is not allowed in strict production."
                    ),
                    blocking=True,
                )
            )
        if (
            strict
            and policy.fail_on_silent_fallback
            and record.fallback_used
            and not record.fallback_disclosed
        ):
            violations.append(
                ProductionModeViolation(
                    stage_kind=record.stage_kind,
                    artifact_id=artifact_id,
                    backend_kind=record.backend_kind,
                    violation_type="silent_fallback",
                    message=f"Stage {record.stage_kind.value} used an undisclosed fallback.",
                    blocking=True,
                )
            )

    blocking = [violation for violation in violations if violation.blocking]
    forbidden_stage_ids = {(violation.stage_kind, violation.artifact_id) for violation in blocking}
    forbidden_count = len(forbidden_stage_ids)
    allowed_count = len(records) if not strict else max(0, len(records) - forbidden_count)
    return ProductionModeReport(
        run_id=run_id,
        report_id=report_id,
        production_mode_check_present=True,
        require_non_fake_backends=strict,
        stage_count=len(records),
        scientific_generation_stage_count=sum(
            record.is_scientific_generation for record in records
        ),
        scientific_judgment_stage_count=sum(record.is_scientific_judgment for record in records),
        execution_or_verification_stage_count=sum(
            record.is_execution_or_verification for record in records
        ),
        violation_count=len(violations),
        blocking_violation_count=len(blocking),
        allowed_stage_count=allowed_count,
        forbidden_stage_count=forbidden_count,
        production_ready=(strict and not blocking and bool(records)),
        policy=policy,
        stage_records=records,
        violations=violations,
        missing_stage_kinds=missing,
        warnings=list(warnings or []),
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def inspect_backends(*, run_id: str, root: str | Path = ".") -> ProductionModeReport:
    """Read-only backend inventory using permissive development policy."""
    records, inferred, warnings = collect_backend_records(run_id=run_id, root=root)
    return evaluate_production_mode(
        run_id=run_id,
        records=records,
        policy=ProductionModePolicy(require_non_fake_backends=False),
        expected_stage_kinds=inferred,
        report_id="backend-inspection-derived",
        warnings=warnings,
    )


def check_production_mode(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    require_non_fake_backends: bool = False,
) -> ProductionModeCheckResult:
    """Evaluate and persist an append-only production-mode report."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    records, inferred, warnings = collect_backend_records(run_id=run_id, root=root_path)
    report_number = _next_number(reports, _REPORT_RE)
    report_id = f"production-mode-report-{report_number:04d}"
    report = evaluate_production_mode(
        run_id=run_id,
        records=records,
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=inferred,
        report_id=report_id,
        warnings=warnings,
    )
    metadata = {
        "stage": "production_mode_check",
        "artifact_role": "backend_authority_audit",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_production_mode_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ],
        action_type=ControllerActionType.PRODUCTION_MODE_CHECKED,
        commit_payload={
            "run_id": run_id,
            "report_id": report_id,
            "require_non_fake_backends": require_non_fake_backends,
            "blocking_violation_count": report.blocking_violation_count,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )
    artifact = next(item for item in persistence.artifacts if item.id == report_id)
    return ProductionModeCheckResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=artifact,
    )


def render_production_mode_text(report: ProductionModeReport) -> str:
    """Render compact backend inventory and production-mode outcome."""
    lines = [
        "Production Mode Check",
        f"Strict non-fake mode: {str(report.require_non_fake_backends).lower()}",
        f"Stages: {report.stage_count}",
        f"Blocking violations: {report.blocking_violation_count}",
        f"Production ready: {str(report.production_ready).lower()}",
        "Backends:",
    ]
    lines.extend(
        f"- {record.stage_kind.value}: {record.backend_kind.value} "
        f"({record.backend_name}) production_allowed={str(record.allowed_in_production).lower()}"
        for record in report.stage_records
    )
    if report.violations:
        lines.append("Violations:")
        lines.extend(f"- {violation.message}" for violation in report.violations)
    lines.append("publication_ready=false")
    return "\n".join(lines)


def render_production_mode_markdown(report: ProductionModeReport) -> str:
    """Render append-only production-mode Markdown report."""
    lines = [
        "# Production Mode Backend Audit",
        "",
        f"Strict non-fake mode: `{str(report.require_non_fake_backends).lower()}`",
        f"Blocking violations: `{report.blocking_violation_count}`",
        f"Production ready: `{str(report.production_ready).lower()}`",
        "",
        "| Stage | Backend kind | Backend | Production allowed |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {record.stage_kind.value} | {record.backend_kind.value} | "
        f"{record.backend_name} | {str(record.allowed_in_production).lower()} |"
        for record in report.stage_records
    )
    if report.violations:
        lines.extend(["", "## Blocking Violations", ""])
        lines.extend(f"- {violation.message}" for violation in report.violations)
    lines.extend(
        [
            "",
            "This report audits backend authority only. It creates no scientific evidence or "
            "publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _infer_legacy_records(
    *,
    path: Path,
    stage_kind: ScientificStageKind,
    payload: dict[str, object],
) -> list[StageBackendRecord]:
    artifact_id = path.stem
    if stage_kind == ScientificStageKind.OPPORTUNITY_DISCOVERY:
        return [
            stage_backend_record(
                stage_id=artifact_id,
                stage_kind=stage_kind,
                backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
                backend_name="deterministic_method_lens_library",
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason=(
                    "M91 derives primitives, questions, and opportunities from fixed local "
                    "templates."
                ),
                artifact_ids=[artifact_id],
            )
        ]
    if stage_kind == ScientificStageKind.VARIANCE_GENERATION:
        return [
            stage_backend_record(
                stage_id=artifact_id,
                stage_kind=stage_kind,
                backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
                backend_name="deterministic_variance_candidate_templates",
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason="M92 generates research branches from fixed candidate-family templates.",
                artifact_ids=[artifact_id],
            )
        ]
    if stage_kind == ScientificStageKind.SUBSTRATE_CONSTRUCTION:
        return [
            stage_backend_record(
                stage_id=artifact_id,
                stage_kind=stage_kind,
                backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
                backend_name="deterministic_scientific_substrate_templates",
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason=(
                    "M93 instantiates models, hypotheses, and experiment designs from templates."
                ),
                artifact_ids=[artifact_id],
            )
        ]
    if stage_kind == ScientificStageKind.BRANCH_ROUTING:
        return [
            stage_backend_record(
                stage_id=artifact_id,
                stage_kind=stage_kind,
                backend_kind=BackendKind.HEURISTIC,
                backend_name="deterministic_branch_route_rules",
                is_scientific_generation=False,
                is_scientific_judgment=True,
                is_execution_or_verification=False,
                reason=(
                    "M94 route selection changes scientific next actions using lexical heuristics."
                ),
                artifact_ids=[artifact_id],
            )
        ]
    if stage_kind == ScientificStageKind.EXPERIMENT_DESIGN:
        spec_ids = [
            str(item.get("spec_id"))
            for item in payload.get("specs", [])
            if isinstance(item, dict) and item.get("spec_id")
        ]
        return [
            stage_backend_record(
                stage_id=artifact_id,
                stage_kind=stage_kind,
                backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
                backend_name="route_execution_spec_templates",
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason="M95 experiment and reduction contracts are fixed deterministic templates.",
                artifact_ids=[artifact_id, *spec_ids],
            )
        ]
    result_ids = [
        str(item.get("result_id"))
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("result_id")
    ]
    return [
        stage_backend_record(
            stage_id=f"{artifact_id}-execution",
            stage_kind=ScientificStageKind.EXPERIMENT_EXECUTION,
            backend_kind=BackendKind.FIXTURE,
            backend_name="fixed_route_result_values",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="M95 returns fixed result values without executing generated experiment code.",
            artifact_ids=[artifact_id, *result_ids],
        ),
        stage_backend_record(
            stage_id=f"{artifact_id}-metrics",
            stage_kind=ScientificStageKind.METRIC_COMPUTATION,
            backend_kind=BackendKind.FIXTURE,
            backend_name="fixed_route_metric_values",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason=(
                "M95 metric values are fixture constants rather than computed execution outputs."
            ),
            artifact_ids=[artifact_id, *result_ids],
        ),
    ]


def _default_production_allowed(
    *,
    stage_kind: ScientificStageKind,
    backend_kind: BackendKind,
    is_scientific_generation: bool,
    is_scientific_judgment: bool,
    is_execution_or_verification: bool,
) -> bool:
    if is_scientific_generation or is_scientific_judgment:
        return backend_kind in {
            BackendKind.LLM_OPENAI,
            BackendKind.LLM_OTHER,
            BackendKind.HUMAN,
            BackendKind.RETRIEVAL_REAL,
        }
    if is_execution_or_verification:
        if stage_kind == ScientificStageKind.METRIC_COMPUTATION:
            return backend_kind == BackendKind.LOCAL_EXECUTION
        return backend_kind in {
            BackendKind.LOCAL_EXECUTION,
            BackendKind.SYMBOLIC_CHECKER,
            BackendKind.RETRIEVAL_REAL,
            BackendKind.HUMAN,
            BackendKind.HEURISTIC,
        }
    return backend_kind not in {
        BackendKind.FAKE,
        BackendKind.FIXTURE,
        BackendKind.DETERMINISTIC_TEMPLATE,
    }


def _record_allowed_by_policy(*, record: StageBackendRecord, policy: ProductionModePolicy) -> bool:
    if record.is_scientific_generation or record.is_scientific_judgment:
        return record.backend_kind not in policy.forbidden_backend_kinds
    if record.stage_kind == ScientificStageKind.EXPERIMENT_EXECUTION:
        return policy.allow_local_execution and record.backend_kind in {
            BackendKind.LOCAL_EXECUTION,
            BackendKind.SYMBOLIC_CHECKER,
        }
    if record.stage_kind == ScientificStageKind.METRIC_COMPUTATION:
        return (
            policy.allow_metric_computation and record.backend_kind == BackendKind.LOCAL_EXECUTION
        )
    if record.stage_kind == ScientificStageKind.CLAIM_AUDIT:
        return policy.allow_claim_audit
    if record.stage_kind == ScientificStageKind.BUNDLE_VERIFICATION:
        return policy.allow_bundle_verification
    return record.allowed_in_production


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for path in directory.iterdir()
        if (match := pattern.match(path.name)) is not None
    ]
    return max(numbers, default=0) + 1


__all__ = [
    "ProductionModeCheckResult",
    "ProductionModeError",
    "check_production_mode",
    "collect_backend_records",
    "evaluate_production_mode",
    "inspect_backends",
    "render_production_mode_markdown",
    "render_production_mode_text",
    "stage_backend_record",
]
