"""Scientific critic ensembles and bounded cross-package paper-nucleus adjudication."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.adapters.errors import AdapterError
from factori.adapters.scientific_critic import ScientificCriticClient
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ControllerActionType,
    CrossPackageAdjudicationInspectionReport,
    CrossPackageAdjudicationReport,
    EvidenceArtifactType,
    EvidencePackageAdjudicationDecision,
    EvidencePackageAdjudicationScore,
    EvidencePackageDecision,
    EvidencePackageExecutionReport,
    HybridEvidencePackageCandidate,
    HybridEvidencePackageReport,
    PaperNucleusSelection,
    ProductionModePolicy,
    ScientificCriticFinding,
    ScientificCriticFindingSeverity,
    ScientificCriticFindingType,
    ScientificCriticRawArtifact,
    ScientificCriticReview,
    ScientificCriticRole,
    ScientificStageKind,
    StageBackendRecord,
)

_PACKAGE_RE = re.compile(r"^hybrid-evidence-package-report-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
_CRITIC_RE = re.compile(r"^scientific-critic-review-report-(\d{4})\.json$")
_ADJUDICATION_RE = re.compile(r"^cross-package-adjudication-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^scientific-critic-raw-(\d{4})\.json$")

_DEFAULT_ROLES = tuple(ScientificCriticRole)
_BLOCKING_TYPES = {
    ScientificCriticFindingType.WEAK_BASELINE,
    ScientificCriticFindingType.MISSING_BASELINE,
    ScientificCriticFindingType.FALSE_BRIDGE,
    ScientificCriticFindingType.DECORATIVE_METHOD_USAGE,
    ScientificCriticFindingType.OVERCLAIM,
    ScientificCriticFindingType.PROOF_OVERSTATED,
    ScientificCriticFindingType.REAL_WORLD_VALIDATION_OVERSTATED,
}
_MANDATORY_FORBIDDEN_CLAIMS = [
    "real-world validation",
    "verified theorem",
    "novelty proven",
    "underuse proven",
    "publication ready",
    "general domain truth",
]
_SYMBOLIC_TYPES = {
    EvidenceArtifactType.SYMBOLIC_REDUCTION,
    EvidenceArtifactType.SYMBOLIC_DERIVATION,
    EvidenceArtifactType.PROOF_PLAN,
}


class EvidencePackageAdjudicationError(RuntimeError):
    """Raised when critic review or cross-package selection cannot proceed safely."""


@dataclass(frozen=True)
class EvidencePackageAdjudicationStageResult:
    run_id: str
    report: CrossPackageAdjudicationReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def critique_evidence_packages(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    critic: ScientificCriticClient,
    require_non_fake_backends: bool = False,
    roles: tuple[ScientificCriticRole, ...] = _DEFAULT_ROLES,
) -> EvidencePackageAdjudicationStageResult:
    """Run an independent LLM critic role over every persisted hybrid evidence package."""
    _validate_critic(critic)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    package_path, package_report = _load_latest_package_report(reports)
    execution_path, execution_report = _load_latest_execution_report(reports)
    _require_unique_package_ids(package_report)
    if require_non_fake_backends and (
        not package_report.production_ready or not execution_report.production_ready
    ):
        raise EvidencePackageAdjudicationError(
            "Strict critic review requires production-eligible hybrid package and execution "
            "reports."
        )
    if not package_report.packages:
        raise EvidencePackageAdjudicationError(
            "No hybrid evidence packages are available for review."
        )

    report_number = _next_number(reports, _CRITIC_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"scientific-critic-review-report-{report_number:04d}"
    results_by_package = _results_by_package(execution_report)
    reviews: list[ScientificCriticReview] = []
    raws: list[ScientificCriticRawArtifact] = []
    warnings: list[str] = []

    for package_index, package in enumerate(package_report.packages, start=1):
        for role_index, role in enumerate(roles, start=1):
            raw_id = f"scientific-critic-raw-{raw_number + len(raws):04d}"
            review_id = (
                f"scientific-critic-review-{report_number:04d}-{_slug(package.package_id)}-"
                f"{role.value}"
            )
            try:
                response = critic.critique_package(
                    prompt_id=f"{report_id}-prompt-{package_index:03d}-{role_index:03d}",
                    critic_role=role,
                    package_payload=package.model_dump(mode="json"),
                    execution_payload=[
                        item.model_dump(mode="json")
                        for item in results_by_package[package.package_id]
                    ],
                )
            except (AdapterError, ValueError) as exc:
                raise EvidencePackageAdjudicationError(
                    f"Scientific critic failed for {package.package_id}/{role.value}: {exc}"
                ) from exc
            rejection_reasons = list(response.rejection_reasons)
            accepted_id: str | None = None
            if response.accepted is not None and not rejection_reasons:
                findings = [
                    ScientificCriticFinding(
                        finding_id=f"{review_id}-finding-{finding_index:03d}",
                        critic_role=role,
                        package_id=package.package_id,
                        **finding.model_dump(mode="python"),
                    )
                    for finding_index, finding in enumerate(response.accepted.findings, start=1)
                ]
                reviews.append(
                    ScientificCriticReview(
                        review_id=review_id,
                        run_id=run_id,
                        package_id=package.package_id,
                        critic_role=role,
                        backend_kind=critic.backend_kind,
                        summary=response.accepted.summary,
                        findings=findings,
                        score_delta=response.accepted.score_delta,
                        recommended_decision=response.accepted.recommended_decision,
                    )
                )
                accepted_id = review_id
            else:
                warnings.append(
                    f"Rejected critic review for {package.package_id}/{role.value}: "
                    + "; ".join(rejection_reasons)
                )
            raws.append(
                ScientificCriticRawArtifact(
                    raw_artifact_id=raw_id,
                    run_id=run_id,
                    operation="critic_review",
                    package_id_optional=package.package_id,
                    critic_role_optional=role,
                    backend_name=critic.backend_name,
                    model=critic.model,
                    prompt_text=response.prompt_text,
                    requested_output_schema=response.requested_output_schema,
                    raw_response=response.raw_response,
                    accepted_id_optional=accepted_id,
                    rejection_reasons=rejection_reasons,
                    fallback_used=critic.fallback_used,
                )
            )

    expected_count = len(package_report.packages) * len(roles)
    if require_non_fake_backends and len(reviews) != expected_count:
        raise EvidencePackageAdjudicationError(
            "Strict critic review requires a valid review for every package and critic role: "
            + " | ".join(warnings)
        )
    if not reviews:
        raise EvidencePackageAdjudicationError("No valid scientific critic reviews were produced.")

    backend_records = [
        _critic_backend_record(report_id, critic, [item.raw_artifact_id for item in raws])
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[
            *package_report.backend_records,
            *execution_report.backend_records,
            *backend_records,
        ],
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=[
            ScientificStageKind.HYBRID_EVIDENCE_PLANNING,
            ScientificStageKind.EXPERIMENT_EXECUTION,
            ScientificStageKind.CRITIC_REVIEW,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if require_non_fake_backends and production.blocking_violation_count:
        raise EvidencePackageAdjudicationError(
            "Strict critic review blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    report = CrossPackageAdjudicationReport(
        run_id=run_id,
        report_id=report_id,
        adjudication_status="completed_with_warnings" if warnings else "completed",
        source_package_report_path=_relative(root_path, package_path),
        source_execution_report_path=_relative(root_path, execution_path),
        critic_review_count=len(reviews),
        adjudicated_package_count=0,
        blocking_finding_count=_blocking_finding_count(reviews),
        reviews=reviews,
        raw_artifact_paths=[f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raws],
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(require_non_fake_backends and not production.blocking_violation_count),
    )
    persistence = _persist_report(
        report=report,
        raw_artifacts=raws,
        store=store,
        ledger=ledger,
        action_type=ControllerActionType.SCIENTIFIC_CRITIC_REVIEWS_WRITTEN,
    )
    return _stage_result(report, persistence)


def adjudicate_evidence_packages(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    critic: ScientificCriticClient,
    require_non_fake_backends: bool = False,
    allow_symbolic_primary: bool = False,
) -> EvidencePackageAdjudicationStageResult:
    """Use critic findings and metadata to choose one bounded paper nucleus when eligible."""
    _validate_critic(critic)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    package_path, package_report = _load_latest_package_report(reports)
    execution_path, execution_report = _load_latest_execution_report(reports)
    critic_path, critic_report = _load_latest_critic_report(reports)
    _require_unique_package_ids(package_report)
    if require_non_fake_backends and not (
        package_report.production_ready
        and execution_report.production_ready
        and critic_report.production_ready
    ):
        raise EvidencePackageAdjudicationError(
            "Strict adjudication requires production-eligible package, execution, and critic "
            "reports."
        )
    if not critic_report.reviews:
        raise EvidencePackageAdjudicationError(
            "No scientific critic reviews are available for adjudication."
        )

    report_number = _next_number(reports, _ADJUDICATION_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"cross-package-adjudication-report-{report_number:04d}"
    results_by_package = _results_by_package(execution_report)
    reviews_by_package = _reviews_by_package(critic_report.reviews)
    scores = [
        _aggregate_score(
            package=package,
            results=results_by_package[package.package_id],
            reviews=reviews_by_package[package.package_id],
        )
        for package in package_report.packages
    ]
    eligibility = {
        package.package_id: _primary_blockers(
            package=package,
            results=results_by_package[package.package_id],
            reviews=reviews_by_package[package.package_id],
            allow_symbolic_primary=allow_symbolic_primary,
        )
        for package in package_report.packages
    }
    try:
        response = critic.adjudicate_packages(
            prompt_id=f"{report_id}-prompt-001",
            packages_payload=[item.model_dump(mode="json") for item in package_report.packages],
            execution_payload=[item.model_dump(mode="json") for item in execution_report.results],
            critic_reviews_payload=[item.model_dump(mode="json") for item in critic_report.reviews],
            score_payload=[item.model_dump(mode="json") for item in scores],
        )
    except (AdapterError, ValueError) as exc:
        raise EvidencePackageAdjudicationError(f"Cross-package adjudication failed: {exc}") from exc

    rejection_reasons = list(response.rejection_reasons)
    decisions: list[EvidencePackageAdjudicationDecision] = []
    nucleus: PaperNucleusSelection | None = None
    if response.accepted is not None and not rejection_reasons:
        try:
            decisions, nucleus = _materialize_adjudication(
                run_id=run_id,
                proposal=response.accepted,
                packages=package_report.packages,
                results_by_package=results_by_package,
                eligibility=eligibility,
            )
        except EvidencePackageAdjudicationError as exc:
            rejection_reasons.append(str(exc))
    if rejection_reasons:
        raise EvidencePackageAdjudicationError(
            "Cross-package adjudication response was rejected: " + "; ".join(rejection_reasons)
        )
    raw = ScientificCriticRawArtifact(
        raw_artifact_id=f"scientific-critic-raw-{raw_number:04d}",
        run_id=run_id,
        operation="cross_package_adjudication",
        backend_name=critic.backend_name,
        model=critic.model,
        prompt_text=response.prompt_text,
        requested_output_schema=response.requested_output_schema,
        raw_response=response.raw_response,
        accepted_id_optional=report_id,
        fallback_used=critic.fallback_used,
    )
    backend_records = [
        _adjudication_backend_record(report_id, critic, [raw.raw_artifact_id]),
        _aggregation_backend_record(report_id, [score.package_id for score in scores]),
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[
            *package_report.backend_records,
            *execution_report.backend_records,
            *critic_report.backend_records,
            *backend_records,
        ],
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=[
            ScientificStageKind.HYBRID_EVIDENCE_PLANNING,
            ScientificStageKind.EXPERIMENT_EXECUTION,
            ScientificStageKind.CRITIC_REVIEW,
            ScientificStageKind.ADJUDICATION,
            ScientificStageKind.ADJUDICATION_SCORE_AGGREGATION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if require_non_fake_backends and production.blocking_violation_count:
        raise EvidencePackageAdjudicationError(
            "Strict cross-package adjudication blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    eligibility_reasons = [reason for items in eligibility.values() for reason in items]
    warnings = (
        [f"No primary nucleus was eligible: {'; '.join(eligibility_reasons)}"]
        if nucleus is None
        else []
    )
    report = CrossPackageAdjudicationReport(
        run_id=run_id,
        report_id=report_id,
        adjudication_status="completed_with_warnings" if warnings else "completed",
        source_package_report_path=_relative(root_path, package_path),
        source_execution_report_path=_relative(root_path, execution_path),
        critic_review_count=len(critic_report.reviews),
        adjudicated_package_count=len(decisions),
        blocking_finding_count=_blocking_finding_count(critic_report.reviews),
        reviews=critic_report.reviews,
        package_rankings=scores,
        decisions=decisions,
        paper_nucleus_selection_optional=nucleus,
        raw_artifact_paths=[f"runs/{run_id}/reports/{raw.raw_artifact_id}.json"],
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(require_non_fake_backends and not production.blocking_violation_count),
    )
    persistence = _persist_report(
        report=report,
        raw_artifacts=[raw],
        store=store,
        ledger=ledger,
        action_type=ControllerActionType.CROSS_PACKAGE_ADJUDICATION_WRITTEN,
    )
    return _stage_result(report, persistence)


def inspect_package_adjudication(
    *, run_id: str, root: str | Path = "."
) -> CrossPackageAdjudicationInspectionReport:
    """Read the latest final M104 adjudication without changing the run."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _ADJUDICATION_RE)
    if path is None:
        return CrossPackageAdjudicationInspectionReport(
            run_id=run_id,
            package_adjudication_present=False,
        )
    report = _load_adjudication_report(path)
    return CrossPackageAdjudicationInspectionReport(
        run_id=run_id,
        package_adjudication_present=True,
        latest_report_id_optional=report.report_id,
        adjudication_status_optional=report.adjudication_status,
        critic_review_count=report.critic_review_count,
        adjudicated_package_count=report.adjudicated_package_count,
        blocking_finding_count=report.blocking_finding_count,
        primary_nucleus_selected=report.paper_nucleus_selection_optional is not None,
        paper_nucleus_selection_optional=report.paper_nucleus_selection_optional,
        reviews=report.reviews,
        package_rankings=report.package_rankings,
        decisions=report.decisions,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def render_package_adjudication_text(report: CrossPackageAdjudicationInspectionReport) -> str:
    """Render a concise human inspection view for M104."""
    nucleus = report.paper_nucleus_selection_optional
    return "\n".join(
        [
            "Package adjudication: "
            + ("present" if report.package_adjudication_present else "absent"),
            f"Status: {report.adjudication_status_optional or 'not available'}",
            f"Critic reviews: {report.critic_review_count}",
            f"Adjudicated packages: {report.adjudicated_package_count}",
            f"Blocking findings: {report.blocking_finding_count}",
            f"Primary nucleus: {nucleus.primary_title if nucleus else 'not selected'}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_package_adjudication_markdown(report: CrossPackageAdjudicationReport) -> str:
    """Render append-only prose context without creating manuscript content or evidence."""
    score_by_package = {score.package_id: score for score in report.package_rankings}
    lines = [
        "# Scientific Critic Ensemble and Cross-Package Adjudication",
        "",
        f"Status: `{report.adjudication_status}`",
        f"Critic reviews: `{report.critic_review_count}`",
        f"Blocking findings: `{report.blocking_finding_count}`",
        "",
        "| Package | Decision | Score |",
        "|---|---|---:|",
    ]
    for decision in report.decisions:
        score = score_by_package.get(decision.package_id)
        score_value = score.final_score if score is not None else 0.0
        lines.append(f"| {decision.package_id} | {decision.decision.value} | {score_value:.3f} |")
    if report.paper_nucleus_selection_optional is not None:
        nucleus = report.paper_nucleus_selection_optional
        lines.extend(
            [
                "",
                "## Bounded Paper Nucleus",
                "",
                f"Primary: `{nucleus.primary_title}`",
                f"Allowed claim scope: {nucleus.allowed_claim_scope}",
                "",
                "The selection is adjudication context only. It creates no evidence, theorem "
                "verification, novelty proof, real-world validation, or publication readiness.",
            ]
        )
    lines.extend(["", "publication_ready=false"])
    return "\n".join(lines)


def _materialize_adjudication(
    *,
    run_id: str,
    proposal: Any,
    packages: list[HybridEvidencePackageCandidate],
    results_by_package: dict[str, list[Any]],
    eligibility: dict[str, list[str]],
) -> tuple[list[EvidencePackageAdjudicationDecision], PaperNucleusSelection | None]:
    package_by_id = {item.package_id: item for item in packages}
    proposal_ids = {item.package_id for item in proposal.decisions}
    if proposal_ids != set(package_by_id):
        missing = sorted(set(package_by_id) - proposal_ids)
        extra = sorted(proposal_ids - set(package_by_id))
        raise EvidencePackageAdjudicationError(
            f"Adjudication must decide every package; missing={missing}, unknown={extra}."
        )
    decisions: list[EvidencePackageAdjudicationDecision] = []
    primary: EvidencePackageAdjudicationDecision | None = None
    for item in proposal.decisions:
        blockers = eligibility[item.package_id]
        if item.decision == EvidencePackageDecision.PRIMARY_NUCLEUS and blockers:
            raise EvidencePackageAdjudicationError(
                f"Package {item.package_id} is ineligible for primary nucleus: "
                f"{'; '.join(blockers)}"
            )
        decision_payload = item.model_dump(mode="python")
        decision_payload["forbidden_claims"] = _merge_forbidden_claims(item.forbidden_claims)
        decision_payload["blocking_findings"] = sorted(
            set([*item.blocking_findings, *blockers])
        )
        decision = EvidencePackageAdjudicationDecision(**decision_payload)
        decisions.append(decision)
        if decision.decision == EvidencePackageDecision.PRIMARY_NUCLEUS:
            if primary is not None:
                raise EvidencePackageAdjudicationError(
                    "Adjudication selected more than one primary nucleus."
                )
            primary = decision
    if primary is None:
        if proposal.paper_nucleus_selection_optional is not None:
            raise EvidencePackageAdjudicationError(
                "Paper nucleus exists without a primary package decision."
            )
        return decisions, None
    nucleus_proposal = proposal.paper_nucleus_selection_optional
    if nucleus_proposal is None or nucleus_proposal.primary_package_id != primary.package_id:
        raise EvidencePackageAdjudicationError(
            "Primary package decision must match paper nucleus selection."
        )
    package = package_by_id[primary.package_id]
    decision_by_kind: dict[EvidencePackageDecision, list[str]] = defaultdict(list)
    for decision in decisions:
        decision_by_kind[decision.decision].append(decision.package_id)
    nucleus = PaperNucleusSelection(
        primary_package_id=primary.package_id,
        primary_substrate_id=package.source_substrate_id,
        primary_title=package.title,
        central_claim_draft=nucleus_proposal.central_claim_draft,
        allowed_claim_scope=nucleus_proposal.allowed_claim_scope,
        forbidden_claims=_merge_forbidden_claims(nucleus_proposal.forbidden_claims),
        supporting_package_ids=decision_by_kind[EvidencePackageDecision.SUPPORTING_PACKAGE],
        appendix_package_ids=decision_by_kind[EvidencePackageDecision.APPENDIX_PACKAGE],
        negative_package_ids=decision_by_kind[EvidencePackageDecision.NEGATIVE_RESULT_PACKAGE],
        rejected_package_ids=[
            *decision_by_kind[EvidencePackageDecision.REJECT_WEAK_PACKAGE],
            *decision_by_kind[EvidencePackageDecision.REJECT_FALSE_BRIDGE],
        ],
        required_repairs_before_manuscript=nucleus_proposal.required_repairs_before_manuscript,
        required_additional_checks=nucleus_proposal.required_additional_checks,
    )
    return decisions, nucleus


def _aggregate_score(
    *,
    package: HybridEvidencePackageCandidate,
    results: list[Any],
    reviews: list[ScientificCriticReview],
) -> EvidencePackageAdjudicationScore:
    plans = package.artifact_plans
    completed = [item for item in results if item.status == "completed"]
    drafts = [item for item in results if item.status == "draft_created"]
    negative = [item for item in results if item.status == "negative_result"]
    metric_completed = [item for item in completed if item.metrics]
    baseline_strength = _ratio(
        sum(bool(item.baseline_or_comparator_plan) for item in plans),
        len(plans),
    )
    control_quality = _ratio(
        sum(
            bool(item.control_plan_optional or item.negative_control_plan_optional)
            for item in plans
        ),
        len(plans),
    )
    robustness_quality = _ratio(
        sum(item.artifact_type == EvidenceArtifactType.ROBUSTNESS_SWEEP for item in plans),
        1,
    )
    symbolic_quality = _ratio(sum(item.artifact_type in _SYMBOLIC_TYPES for item in plans), 1)
    retrieval_quality = _ratio(
        sum(item.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK for item in plans),
        1,
    )
    evidence_maturity = min(1.0, (len(completed) + 0.35 * len(drafts)) / max(1, len(plans)))
    effect_strength = min(1.0, len(metric_completed) / max(1, len(plans)))
    failure_mode_value = min(1.0, len(negative) / max(1, len(plans)))
    critic_signal = _mean([review.score_delta for review in reviews])
    base_signal = _clamp(0.5 + 0.5 * critic_signal)
    findings = [finding for review in reviews for finding in review.findings]
    tautology_penalty = _finding_penalty(
        findings,
        {
            ScientificCriticFindingType.TAUTOLOGICAL_RESULT,
            ScientificCriticFindingType.RIGGED_DGP,
        },
    )
    false_bridge_penalty = _finding_penalty(
        findings,
        {
            ScientificCriticFindingType.FALSE_BRIDGE,
            ScientificCriticFindingType.DECORATIVE_METHOD_USAGE,
        },
    )
    overclaim_penalty = _finding_penalty(
        findings,
        {
            ScientificCriticFindingType.OVERCLAIM,
            ScientificCriticFindingType.PROOF_OVERSTATED,
            ScientificCriticFindingType.REAL_WORLD_VALIDATION_OVERSTATED,
            ScientificCriticFindingType.NOVELTY_OVERSTATED,
        },
    )
    complexity_penalty = _clamp(max(0, len(plans) - 4) / 10)
    scope_safety = _clamp(1.0 - overclaim_penalty)
    final_score = _clamp(
        0.15 * effect_strength
        + 0.17 * evidence_maturity
        + 0.10 * baseline_strength
        + 0.08 * control_quality
        + 0.05 * robustness_quality
        + 0.05 * symbolic_quality
        + 0.03 * retrieval_quality
        + 0.05 * failure_mode_value
        + 0.12 * base_signal
        + 0.08 * scope_safety
        + 0.12 * base_signal
        - 0.05 * complexity_penalty
        - 0.15 * tautology_penalty
        - 0.18 * false_bridge_penalty
        - 0.15 * overclaim_penalty
    )
    return EvidencePackageAdjudicationScore(
        package_id=package.package_id,
        effect_strength=effect_strength,
        evidence_maturity=evidence_maturity,
        baseline_strength=baseline_strength,
        control_quality=control_quality,
        robustness_quality=robustness_quality,
        symbolic_quality=symbolic_quality,
        retrieval_quality=retrieval_quality,
        failure_mode_value=failure_mode_value,
        novelty_potential=base_signal,
        paper_coherence=base_signal,
        technical_plausibility=base_signal,
        interpretability=base_signal,
        scope_safety=scope_safety,
        complexity_penalty=complexity_penalty,
        tautology_penalty=tautology_penalty,
        false_bridge_penalty=false_bridge_penalty,
        overclaim_penalty=overclaim_penalty,
        final_score=final_score,
        score_explanation=(
            "Local aggregation combines execution-status metadata with recorded LLM critic "
            "deltas and applies explicit finding penalties; it creates no evidence or validation."
        ),
    )


def _primary_blockers(
    *,
    package: HybridEvidencePackageCandidate,
    results: list[Any],
    reviews: list[ScientificCriticReview],
    allow_symbolic_primary: bool,
) -> list[str]:
    blockers = [
        finding.finding_id
        for review in reviews
        for finding in review.findings
        if finding.blocking or finding.finding_type in _BLOCKING_TYPES
    ]
    completed_or_negative = [
        item for item in results if item.status in {"completed", "negative_result"}
    ]
    if not completed_or_negative:
        blockers.append("no_executed_or_checkable_artifact")
    if (
        not allow_symbolic_primary
        and package.artifact_plans
        and all(plan.artifact_type in _SYMBOLIC_TYPES for plan in package.artifact_plans)
    ):
        blockers.append("symbolic_draft_only")
    negative_controls = [
        item for item in results if item.artifact_type == EvidenceArtifactType.NEGATIVE_CONTROL
    ]
    claim_lower = package.primary_claim_draft.lower()
    intended_failure = "counterexample" in claim_lower or "failure regime" in claim_lower
    if not intended_failure and any(item.status != "completed" for item in negative_controls):
        blockers.append("negative_control_failed")
    return sorted(set(blockers))


def _persist_report(
    *,
    report: CrossPackageAdjudicationReport,
    raw_artifacts: list[ScientificCriticRawArtifact],
    store: ArtifactStore,
    ledger: ResearchLedger,
    action_type: ControllerActionType,
) -> PersistenceResult:
    metadata = _metadata("scientific_critic_adjudication")
    specs = [
        ArtifactWriteSpec(item.raw_artifact_id, ArtifactType.REPORT, item, "json", metadata)
        for item in raw_artifacts
    ]
    specs.extend(
        [
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_package_adjudication_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ]
    )
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=action_type,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "critic_review_count": report.critic_review_count,
            "adjudicated_package_count": report.adjudicated_package_count,
            "publication_ready": False,
        },
    )


def _stage_result(
    report: CrossPackageAdjudicationReport, persistence: PersistenceResult
) -> EvidencePackageAdjudicationStageResult:
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return EvidencePackageAdjudicationStageResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _critic_backend_record(
    report_id: str, critic: ScientificCriticClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-critic-ensemble",
        stage_kind=ScientificStageKind.CRITIC_REVIEW,
        backend_kind=critic.backend_kind,
        backend_name=critic.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason="Independent scientific criticism comes from the recorded non-fake LLM backend.",
        artifact_ids=[report_id, *raw_ids],
        fallback_used=critic.fallback_used,
        fallback_disclosed=critic.fallback_disclosed,
    )


def _adjudication_backend_record(
    report_id: str, critic: ScientificCriticClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-llm-adjudication",
        stage_kind=ScientificStageKind.ADJUDICATION,
        backend_kind=critic.backend_kind,
        backend_name=critic.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason=(
            "Cross-package paper-nucleus selection comes from the recorded non-fake LLM backend."
        ),
        artifact_ids=[report_id, *raw_ids],
        fallback_used=critic.fallback_used,
        fallback_disclosed=critic.fallback_disclosed,
    )


def _aggregation_backend_record(report_id: str, package_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-score-aggregation",
        stage_kind=ScientificStageKind.ADJUDICATION_SCORE_AGGREGATION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="evidence_package_metadata_aggregator",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local aggregation combines persisted metadata and LLM critic deltas without "
            "authoring judgments."
        ),
        artifact_ids=[report_id, *package_ids],
    )


def _validate_critic(critic: ScientificCriticClient) -> None:
    if critic.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise EvidencePackageAdjudicationError(
            "Scientific criticism requires a non-fake LLM backend."
        )
    if critic.fallback_used:
        raise EvidencePackageAdjudicationError(
            "Scientific criticism forbids deterministic fallback."
        )


def _load_latest_package_report(reports: Path) -> tuple[Path, HybridEvidencePackageReport]:
    path = _latest_matching(reports, _PACKAGE_RE)
    if path is None:
        raise EvidencePackageAdjudicationError("No hybrid evidence package report found.")
    return path, _load_model(path, HybridEvidencePackageReport)


def _load_latest_execution_report(reports: Path) -> tuple[Path, EvidencePackageExecutionReport]:
    path = _latest_matching(reports, _EXECUTION_RE)
    if path is None:
        raise EvidencePackageAdjudicationError("No hybrid evidence execution report found.")
    return path, _load_model(path, EvidencePackageExecutionReport)


def _load_latest_critic_report(reports: Path) -> tuple[Path, CrossPackageAdjudicationReport]:
    path = _latest_matching(reports, _CRITIC_RE)
    if path is None:
        raise EvidencePackageAdjudicationError(
            "No scientific critic review report found; run critique-evidence-packages first."
        )
    return path, _load_model(path, CrossPackageAdjudicationReport)


def _load_adjudication_report(path: Path) -> CrossPackageAdjudicationReport:
    return _load_model(path, CrossPackageAdjudicationReport)


def _load_model(path: Path, model_type: Any) -> Any:
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidencePackageAdjudicationError(f"Could not load {path.name}: {exc}") from exc


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir() if pattern.match(path.name)]
    return max(matches, key=lambda path: path.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    matches = [
        int(match.group(1)) for path in directory.glob("*") if (match := pattern.match(path.name))
    ]
    return max(matches, default=0) + 1


def _results_by_package(report: EvidencePackageExecutionReport) -> dict[str, list[Any]]:
    results: dict[str, list[Any]] = defaultdict(list)
    for result in report.results:
        results[result.package_id].append(result)
    return results


def _require_unique_package_ids(report: HybridEvidencePackageReport) -> None:
    package_ids = [item.package_id for item in report.packages]
    if len(package_ids) != len(set(package_ids)):
        raise EvidencePackageAdjudicationError(
            "Hybrid evidence package report contains duplicate package IDs and cannot be "
            "adjudicated safely. Regenerate packages append-only."
        )


def _reviews_by_package(
    reviews: list[ScientificCriticReview],
) -> dict[str, list[ScientificCriticReview]]:
    result: dict[str, list[ScientificCriticReview]] = defaultdict(list)
    for review in reviews:
        result[review.package_id].append(review)
    return result


def _blocking_finding_count(reviews: list[ScientificCriticReview]) -> int:
    return sum(finding.blocking for review in reviews for finding in review.findings)


def _merge_forbidden_claims(values: list[str]) -> list[str]:
    normalized = {value.strip().lower() for value in values}
    return [*values, *[value for value in _MANDATORY_FORBIDDEN_CLAIMS if value not in normalized]]


def _finding_penalty(
    findings: list[ScientificCriticFinding], types: set[ScientificCriticFindingType]
) -> float:
    matching = [item for item in findings if item.finding_type in types]
    severity = {
        ScientificCriticFindingSeverity.INFO: 0.05,
        ScientificCriticFindingSeverity.WARNING: 0.18,
        ScientificCriticFindingSeverity.MAJOR: 0.45,
        ScientificCriticFindingSeverity.BLOCKING: 1.0,
    }
    return _clamp(sum(severity[item.severity] for item in matching) / 2)


def _ratio(numerator: int, denominator: int) -> float:
    return _clamp(numerator / max(1, denominator))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _relative(root_path: Path, path: Path) -> str:
    return path.relative_to(root_path).as_posix()


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.lower())) or "package"


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "is_verification_evidence": False,
    }


__all__ = [
    "EvidencePackageAdjudicationError",
    "EvidencePackageAdjudicationStageResult",
    "adjudicate_evidence_packages",
    "critique_evidence_packages",
    "inspect_package_adjudication",
    "render_package_adjudication_markdown",
    "render_package_adjudication_text",
]
