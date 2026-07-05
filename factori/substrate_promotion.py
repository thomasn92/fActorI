"""Diversity-constrained promotion of variance candidates to scientific substrates."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    ScientificSubstrate,
    ScientificSubstrateAssumption,
    ScientificSubstrateBuildReport,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
    SubstratePromotionCandidate,
    SubstratePromotionConfig,
    SubstratePromotionDecision,
    SubstratePromotionInspectionReport,
    SubstratePromotionReport,
    VarianceAugmentationReport,
    VarianceAugmentedCandidate,
)

_PROMOTION_RE = re.compile(r"^substrate-promotion-(\d{4})\.json$")
_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")
_VARIANCE_RE = re.compile(r"^variance-augmentation-(\d{4})\.json$")
_VARIANCE_APPLICATION_RE = re.compile(
    r"^variance-augmentation-application-(\d{4})\.json$"
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_PREFERRED_FAMILY = {
    "optimal_transport": "robustness_variant",
    "matrix_factorization": "representation_variant",
    "graph_curvature": "mechanism_variant",
    "topological_data_analysis": "robustness_variant",
    "agent_based_modeling": "mechanism_variant",
    "spatial_statistics": "mechanism_variant",
    "network_science": "robustness_variant",
    "kernel_methods": "mechanism_variant",
}


class SubstratePromotionError(RuntimeError):
    """Raised when variance candidates cannot be promoted safely."""


@dataclass(frozen=True)
class SubstratePromotionResult:
    """Persisted substrate-promotion result."""

    run_id: str
    report: SubstratePromotionReport
    build_report: ScientificSubstrateBuildReport
    substrates: list[ScientificSubstrate]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    substrate_artifacts: list[ArtifactRef]


@dataclass(frozen=True)
class _Template:
    title: str
    model_type: str
    equations: list[str]
    variables: list[tuple[str, str, str]]
    domain_problem: str
    central_tension: str
    mechanism: str
    dgp: str
    baseline: str
    method: str
    hypothesis: str
    metrics: list[str]
    ablation: str
    limitations: list[str]
    failure_modes: list[str]


def promote_variance_substrates(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_substrates: int = 8,
) -> SubstratePromotionResult:
    """Promote a diverse selected variance subset to concrete substrates."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise SubstratePromotionError(f"Reports directory not found for run_id={run_id}.")
    config = SubstratePromotionConfig(max_substrates=max_substrates)
    source_path, variance_report = _load_latest_variance_report(reports)
    selected = [
        candidate
        for candidate in variance_report.candidates
        if candidate.selected_for_idea_tree
    ]
    if not selected:
        raise SubstratePromotionError(
            "Latest variance augmentation has no selected candidates to promote."
        )

    promotion_number = _next_number(reports, _PROMOTION_RE)
    promotion_id = f"substrate-promotion-{promotion_number:04d}"
    build_number = _next_number(reports, _BUILD_RE)
    build_id = f"scientific-substrate-build-{build_number:04d}"
    scored = _score_candidates(selected)
    promoted_ids = _select_for_promotion(scored, config)
    promoted_scored = sorted(
        (candidate for candidate in scored if candidate.candidate_id in promoted_ids),
        key=_promotion_rank,
    )
    original_by_id = {candidate.candidate_id: candidate for candidate in selected}
    substrates = [
        _build_substrate(
            run_id=run_id,
            promotion_id=promotion_id,
            candidate=original_by_id[candidate.candidate_id],
            selected_for_next_experiment=(index == 1),
        )
        for index, candidate in enumerate(promoted_scored, start=1)
    ]
    substrate_artifact_ids = [
        _substrate_artifact_id(build_number, index, substrate)
        for index, substrate in enumerate(substrates, start=1)
    ]
    substrate_paths = [
        f"runs/{run_id}/reports/{artifact_id}.json"
        for artifact_id in substrate_artifact_ids
    ]
    substrate_by_candidate = {
        substrate.source_variance_candidate_id_optional: (substrate, path)
        for substrate, path in zip(substrates, substrate_paths, strict=True)
    }
    decisions = _decisions(scored, promoted_scored, substrate_by_candidate)
    method_coverage = len({candidate.method_lens for candidate in promoted_scored})
    family_coverage = len({candidate.branch_family for candidate in promoted_scored})
    warnings: list[str] = []
    available_method_count = len({candidate.method_lens for candidate in scored})
    available_family_count = len({candidate.branch_family for candidate in scored})
    required_methods = min(config.min_method_lenses, available_method_count, max_substrates)
    required_families = min(config.min_branch_families, available_family_count, max_substrates)
    if method_coverage < required_methods:
        warnings.append("Method-lens coverage target could not be met within the capacity limit.")
    if family_coverage < required_families:
        warnings.append("Branch-family coverage target could not be met within the capacity limit.")

    build_report_path = f"runs/{run_id}/reports/{build_id}.json"
    build_report = ScientificSubstrateBuildReport(
        run_id=run_id,
        build_id=build_id,
        build_status="completed_with_warnings" if warnings else "completed",
        source_idea_space_report_path_optional=None,
        source_idea_tree_report_path_optional=None,
        requested_mutation_axis_optional=None,
        max_substrates=max_substrates,
        recommended_mutation_axes=[candidate.title for candidate in scored],
        built_mutation_axes=[candidate.title for candidate in promoted_scored],
        substrate_paths=substrate_paths,
        substrate_count=len(substrates),
        selected_substrate_id_optional=(substrates[0].substrate_id if substrates else None),
        selected_substrate_title_optional=(substrates[0].title if substrates else None),
        pca_low_rank_substrate_id_optional=next(
            (
                substrate.substrate_id
                for substrate in substrates
                if substrate.source_method_lens_id_optional == "matrix_factorization"
            ),
            None,
        ),
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    report = SubstratePromotionReport(
        run_id=run_id,
        promotion_id=promotion_id,
        source_variance_augmentation_path=_relative(root_path, source_path),
        source_variance_augmentation_id=variance_report.augmentation_id,
        scientific_substrate_build_report_path=build_report_path,
        config=config,
        selected_variance_candidate_count=len(selected),
        evaluated_candidate_count=len(scored),
        promoted_substrate_count=len(substrates),
        rejected_candidate_count=len(scored) - len(substrates),
        method_lens_coverage=method_coverage,
        branch_family_coverage=family_coverage,
        promoted_candidate_ids=[candidate.candidate_id for candidate in promoted_scored],
        created_substrate_ids=[substrate.substrate_id for substrate in substrates],
        created_substrate_paths=substrate_paths,
        candidates=scored,
        decisions=decisions,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    metadata = {
        "stage": "substrate_promotion",
        "artifact_role": "scientific_substrate_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    specs: list[ArtifactWriteSpec] = [
        ArtifactWriteSpec(
            artifact_id,
            ArtifactType.REPORT,
            substrate,
            "json",
            metadata,
        )
        for artifact_id, substrate in zip(
            substrate_artifact_ids, substrates, strict=True
        )
    ]
    specs.extend(
        [
            ArtifactWriteSpec(build_id, ArtifactType.REPORT, build_report, "json", metadata),
            ArtifactWriteSpec(promotion_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{promotion_id}-markdown",
                ArtifactType.REPORT,
                render_substrate_promotion_markdown(report, substrates),
                "markdown",
                metadata,
                filename_stem=promotion_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.VARIANCE_SUBSTRATES_PROMOTED,
        commit_payload={
            "run_id": run_id,
            "promotion_id": promotion_id,
            "source_variance_augmentation_id": variance_report.augmentation_id,
            "promoted_substrate_count": len(substrates),
            "method_lens_coverage": method_coverage,
            "branch_family_coverage": family_coverage,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return SubstratePromotionResult(
        run_id=run_id,
        report=report,
        build_report=build_report,
        substrates=substrates,
        persistence=persistence,
        report_artifact=by_id[promotion_id],
        substrate_artifacts=[by_id[artifact_id] for artifact_id in substrate_artifact_ids],
    )


def inspect_substrate_promotion(
    *, run_id: str, root: str | Path = "."
) -> SubstratePromotionInspectionReport:
    """Inspect the latest substrate promotion without mutation."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    path = _latest_matching(reports, _PROMOTION_RE)
    if path is None:
        return SubstratePromotionInspectionReport(
            run_id=run_id,
            substrate_promotion_present=False,
            warnings=["No substrate promotion report is present."],
            publication_ready=False,
        )
    try:
        report = SubstratePromotionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise SubstratePromotionError(f"Could not load substrate promotion: {exc}") from exc
    promoted = [decision for decision in report.decisions if decision.promoted]
    rejected = [decision for decision in report.decisions if not decision.promoted]
    links_present = bool(promoted) and all(
        decision.created_substrate_id_optional
        and decision.created_substrate_path_optional
        for decision in promoted
    )
    return SubstratePromotionInspectionReport(
        run_id=run_id,
        substrate_promotion_present=True,
        latest_promotion_id_optional=report.promotion_id,
        promoted_substrate_count=report.promoted_substrate_count,
        rejected_candidate_count=report.rejected_candidate_count,
        method_lens_coverage=report.method_lens_coverage,
        branch_family_coverage=report.branch_family_coverage,
        idea_tree_substrate_links_present=links_present,
        promoted_candidates=promoted,
        rejected_candidates=rejected,
        report_optional=report,
        warnings=report.warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def render_substrate_promotion_text(report: SubstratePromotionInspectionReport) -> str:
    """Render a compact promotion inspection."""
    if not report.substrate_promotion_present:
        return "\n".join(
            [
                "Substrate promotion: absent",
                *[f"Warning: {warning}" for warning in report.warnings],
                "Publication ready: false",
            ]
        )
    lines = [
        "Variance Substrate Promotion",
        f"Promoted substrates: {report.promoted_substrate_count}",
        f"Rejected candidates: {report.rejected_candidate_count}",
        f"Method-lens coverage: {report.method_lens_coverage}",
        f"Branch-family coverage: {report.branch_family_coverage}",
        (
            "IdeaTree substrate links: "
            f"{str(report.idea_tree_substrate_links_present).lower()}"
        ),
        "Promoted:",
    ]
    lines.extend(
        f"- {decision.method_lens} / {decision.branch_family}: {decision.candidate_id}"
        for decision in report.promoted_candidates
    )
    lines.extend(
        [
            "This report is substrate-planning context only and creates no evidence.",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_substrate_promotion_markdown(
    report: SubstratePromotionReport,
    substrates: list[ScientificSubstrate],
) -> str:
    """Render the append-only promotion Markdown companion."""
    substrate_by_id = {substrate.substrate_id: substrate for substrate in substrates}
    lines = [
        "# Diversity-Constrained Substrate Promotion",
        "",
        f"Promotion ID: `{report.promotion_id}`",
        f"Promoted substrates: `{report.promoted_substrate_count}`",
        f"Method-lens coverage: `{report.method_lens_coverage}`",
        f"Branch-family coverage: `{report.branch_family_coverage}`",
        "",
        "## Decisions",
        "",
    ]
    for decision in report.decisions:
        label = "promoted" if decision.promoted else "rejected"
        lines.append(
            f"- `{decision.candidate_id}` ({decision.method_lens}, "
            f"{decision.branch_family}): **{label}** - {decision.reason}"
        )
        if decision.created_substrate_id_optional:
            substrate = substrate_by_id[decision.created_substrate_id_optional]
            lines.append(f"  - Substrate: {substrate.title}")
    lines.extend(
        [
            "",
            "Promotion creates scientific planning context only. It does not create experiment "
            "or proof evidence, scientific validation, or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _score_candidates(
    candidates: list[VarianceAugmentedCandidate],
) -> list[SubstratePromotionCandidate]:
    fingerprints = Counter(_semantic_fingerprint(candidate) for candidate in candidates)
    method_counts = Counter(candidate.method_lens for candidate in candidates)
    family_counts = Counter(candidate.variant_family for candidate in candidates)
    total = max(1, len(candidates))
    scored: list[SubstratePromotionCandidate] = []
    for candidate in candidates:
        template_available = _template_for(candidate) is not None
        buildability = 1.0 if template_available else 0.72
        feasibility = _verification_feasibility(candidate)
        duplicate_penalty = (
            0.35 if fingerprints[_semantic_fingerprint(candidate)] > 1 else 0.0
        )
        method_coverage = 1.0 - (method_counts[candidate.method_lens] - 1) / total
        family_coverage = 1.0 - (family_counts[candidate.variant_family] - 1) / total
        total_score = _bounded(
            0.20 * candidate.easy_win_score
            + 0.20 * candidate.scientific_interest_score
            + 0.20 * buildability
            + 0.10 * method_coverage
            + 0.10 * family_coverage
            + 0.20 * feasibility
            - 0.15 * duplicate_penalty
        )
        rejection_reasons: list[str] = []
        if buildability < 0.60:
            rejection_reasons.append("candidate lacks a buildable model object")
        if feasibility < 0.60:
            rejection_reasons.append("verification path is not locally feasible")
        scored.append(
            SubstratePromotionCandidate(
                candidate_id=candidate.candidate_id,
                source_opportunity_id=candidate.source_opportunity_id,
                source_method_lens_id=candidate.source_method_lens_id,
                method_lens=candidate.method_lens,
                branch_family=candidate.variant_family,
                title=candidate.title,
                easy_win_score=candidate.easy_win_score,
                scientific_interest_score=candidate.scientific_interest_score,
                substrate_buildability=buildability,
                method_lens_coverage_score=_bounded(method_coverage),
                branch_family_coverage_score=_bounded(family_coverage),
                duplicate_penalty=duplicate_penalty,
                verification_path_feasibility=feasibility,
                total_score=total_score,
                eligible=not rejection_reasons,
                rejection_reasons=rejection_reasons,
            )
        )
    return sorted(scored, key=_promotion_rank)


def _select_for_promotion(
    candidates: list[SubstratePromotionCandidate],
    config: SubstratePromotionConfig,
) -> set[str]:
    eligible = [candidate for candidate in candidates if candidate.eligible]
    by_method: dict[str, list[SubstratePromotionCandidate]] = {}
    for candidate in eligible:
        by_method.setdefault(candidate.source_method_lens_id, []).append(candidate)
    for values in by_method.values():
        values.sort(key=_promotion_rank)
    method_order = sorted(
        by_method,
        key=lambda method_id: _promotion_rank(by_method[method_id][0]),
    )
    selected: list[SubstratePromotionCandidate] = []
    for method_id in method_order:
        if len(selected) >= config.max_substrates:
            break
        preferred = _PREFERRED_FAMILY.get(method_id)
        choice = next(
            (
                candidate
                for candidate in by_method[method_id]
                if candidate.branch_family == preferred
            ),
            by_method[method_id][0],
        )
        selected.append(choice)
    selected_ids = {candidate.candidate_id for candidate in selected}
    selected_families = {candidate.branch_family for candidate in selected}
    available_families = {candidate.branch_family for candidate in eligible}
    required_families = min(
        config.min_branch_families,
        len(available_families),
        config.max_substrates,
    )
    if len(selected_families) < required_families:
        for candidate in eligible:
            if len(selected_families) >= required_families:
                break
            if candidate.branch_family in selected_families:
                continue
            same_method = next(
                (
                    item
                    for item in selected
                    if item.source_method_lens_id == candidate.source_method_lens_id
                ),
                None,
            )
            if same_method is not None:
                selected.remove(same_method)
                selected_ids.remove(same_method.candidate_id)
                selected_families = {item.branch_family for item in selected}
            selected.append(candidate)
            selected_ids.add(candidate.candidate_id)
            selected_families.add(candidate.branch_family)
    for candidate in eligible:
        if len(selected_ids) >= config.max_substrates:
            break
        if candidate.candidate_id not in selected_ids:
            selected_ids.add(candidate.candidate_id)
    return selected_ids


def _decisions(
    scored: list[SubstratePromotionCandidate],
    promoted: list[SubstratePromotionCandidate],
    substrate_by_candidate: dict[str | None, tuple[ScientificSubstrate, str]],
) -> list[SubstratePromotionDecision]:
    rank_by_id = {candidate.candidate_id: index for index, candidate in enumerate(promoted, 1)}
    decisions: list[SubstratePromotionDecision] = []
    for candidate in scored:
        linked = substrate_by_candidate.get(candidate.candidate_id)
        promoted_candidate = candidate.candidate_id in rank_by_id
        if promoted_candidate:
            reason = (
                "Promoted by composite score with method-lens and branch-family coverage."
            )
        elif not candidate.eligible:
            reason = "; ".join(candidate.rejection_reasons)
        else:
            reason = "Not promoted within capacity after diversity coverage was satisfied."
        decisions.append(
            SubstratePromotionDecision(
                candidate_id=candidate.candidate_id,
                method_lens=candidate.method_lens,
                branch_family=candidate.branch_family,
                promoted=promoted_candidate,
                promotion_rank=rank_by_id.get(candidate.candidate_id),
                score_breakdown={
                    "easy_win_score": candidate.easy_win_score,
                    "scientific_interest_score": candidate.scientific_interest_score,
                    "substrate_buildability": candidate.substrate_buildability,
                    "method_lens_coverage_score": candidate.method_lens_coverage_score,
                    "branch_family_coverage_score": candidate.branch_family_coverage_score,
                    "duplicate_penalty": candidate.duplicate_penalty,
                    "verification_path_feasibility": (
                        candidate.verification_path_feasibility
                    ),
                    "total_score": candidate.total_score,
                },
                reason=reason,
                created_substrate_id_optional=(linked[0].substrate_id if linked else None),
                created_substrate_path_optional=(linked[1] if linked else None),
            )
        )
    return decisions


def _build_substrate(
    *,
    run_id: str,
    promotion_id: str,
    candidate: VarianceAugmentedCandidate,
    selected_for_next_experiment: bool,
) -> ScientificSubstrate:
    template = _template_for(candidate) or _generic_template(candidate)
    variables = [
        ScientificSubstrateVariable(symbol=symbol, definition=definition, role=role)
        for symbol, definition, role in template.variables
    ]
    baseline_metrics = [f"baseline_{_slug(metric)}" for metric in template.metrics]
    method_metrics = [f"method_{_slug(metric)}" for metric in template.metrics]
    required_columns = ["seed", "sample_count", *baseline_metrics, *method_metrics]
    return ScientificSubstrate(
        substrate_id=(
            f"scientific-substrate-{promotion_id}-"
            f"{candidate.source_method_lens_id}-{candidate.variant_family}"
        ),
        run_id=run_id,
        source_idea_node_id_optional=candidate.candidate_id,
        source_mutation_axis_optional=(
            f"{candidate.method_lens} / {candidate.variant_family} / {template.title}"
        ),
        source_variance_candidate_id_optional=candidate.candidate_id,
        source_opportunity_id_optional=candidate.source_opportunity_id,
        source_method_lens_id_optional=candidate.source_method_lens_id,
        title=template.title,
        domain=candidate.domain,
        domain_problem=template.domain_problem,
        central_tension=template.central_tension,
        concrete_model_object=ScientificSubstrateModelObject(
            model_type=template.model_type,
            equations=template.equations,
            algorithm_optional=(
                f"Instantiate the declared model, fit or compute it under fixed seeds, and "
                f"compare with {template.baseline}."
            ),
            parameter_interpretation=[
                f"{variable.symbol}: {variable.definition}" for variable in variables
            ],
            identifiability_notes=(
                "Identification is evaluated only in the declared synthetic construction; "
                "parameters are not interpreted as real-world estimates."
            ),
            what_would_falsify_it=(
                f"The method fails the declared metrics against {template.baseline}, or the "
                "effect disappears under its stated stress test."
            ),
        ),
        variables_and_notation=variables,
        assumptions=[
            ScientificSubstrateAssumption(
                assumption_id="synthetic-scope",
                statement="The initial experiment uses fixed-seed synthetic data only.",
                rationale="The candidate declares a locally feasible verification path.",
                violation_consequence=(
                    "Results cannot support the mapped bounded synthetic claim."
                ),
            ),
            ScientificSubstrateAssumption(
                assumption_id="baseline-comparability",
                statement=(
                    "Method and baseline use the same generated sample and evaluation split."
                ),
                rationale="A bounded comparison requires matched inputs and metrics.",
                violation_consequence="The comparison becomes inconclusive.",
            ),
        ],
        mechanism=template.mechanism,
        dgp_or_dataset=template.dgp,
        baseline=template.baseline,
        measurable_hypothesis=template.hypothesis,
        experiment_design=ScientificSubstrateExperimentDesign(
            target_claim=(
                f"For the configured synthetic run, {template.method} reports the declared "
                f"bounded metric pattern relative to {template.baseline}."
            ),
            data_regime="SyntheticOnly",
            dgp=template.dgp,
            train_test_split_optional=(
                "Use a deterministic train/test split where fitting is required."
            ),
            baseline=template.baseline,
            method=template.method,
            metrics=template.metrics,
            seed_plan="Use fixed seeds recorded in any future experiment artifact.",
            ablation_or_stress_test=template.ablation,
            success_criterion=(
                "The method improves or stabilizes the declared metrics relative to baseline "
                "within the configured synthetic scope."
            ),
            failure_criterion=(
                "Metrics do not improve, stability degrades, required outputs are absent, or "
                "the result is negative/inconclusive."
            ),
        ),
        result_schema=ScientificSubstrateResultSchema(
            baseline_metric_names=baseline_metrics,
            method_metric_names=method_metrics,
            comparison_direction=(
                "lower error or instability and higher recovery/detection/stability are better"
            ),
            required_table_columns=required_columns,
            claim_supported_if=(
                "All required metrics are present and satisfy the candidate-specific bounded "
                "success criterion for the configured synthetic run."
            ),
            claim_not_supported_if=(
                "Any required metric is absent, the declared comparison fails, or the run is "
                "negative, failed, or inconclusive."
            ),
        ),
        limitations=template.limitations,
        failure_modes=template.failure_modes,
        evidence_boundary=(
            "Promotion instantiates a scientific planning object only. It creates no evidence; "
            "only a later completed artifact that passes intake may support its mapped bounded "
            "synthetic claim."
        ),
        selected_for_next_experiment=selected_for_next_experiment,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _template_for(candidate: VarianceAugmentedCandidate) -> _Template | None:
    if "human geography" not in candidate.domain.lower():
        return None
    return _HUMAN_GEOGRAPHY_TEMPLATES.get(candidate.source_method_lens_id)


def _generic_template(candidate: VarianceAugmentedCandidate) -> _Template:
    return _Template(
        title=candidate.title,
        model_type=f"promoted_{candidate.source_method_lens_id}",
        equations=[candidate.theory_object],
        variables=[
            ("X", "synthetic input object", "input"),
            ("Y", "bounded target or diagnostic", "response"),
            ("theta", "method parameter", "parameter"),
        ],
        domain_problem=(
            f"The {candidate.domain} opportunity needs a concrete {candidate.method_lens} object."
        ),
        central_tension=(
            f"The method must outperform or clarify {candidate.baseline} without broad claims."
        ),
        mechanism=candidate.model_hint,
        dgp=candidate.experiment_or_proof_plan,
        baseline=candidate.baseline,
        method=candidate.theory_object,
        hypothesis=candidate.hypothesis,
        metrics=["primary_metric", "secondary_metric"],
        ablation=candidate.failure_mode,
        limitations=[
            "The deterministic fallback substrate remains synthetic and domain-bounded.",
            "Promotion does not establish novelty, correctness, or empirical validation.",
        ],
        failure_modes=[candidate.failure_mode],
    )


_HUMAN_GEOGRAPHY_TEMPLATES: dict[str, _Template] = {
    "optimal_transport": _Template(
        title="Wasserstein Robustness of Synthetic Spatial Accessibility Rankings",
        model_type="wasserstein_accessibility_robustness",
        equations=[
            "a_i = sum_j w_j exp(-gamma d_ij)",
            "A_i(rho) = inf_{Q: W(Q,P) <= rho} E_Q[a_i]",
        ],
        variables=[
            ("a_i", "nominal accessibility for region i", "response"),
            ("w_j", "destination attractiveness or demand", "weight"),
            ("d_ij", "distance from i to j", "distance"),
            ("gamma", "distance impedance", "parameter"),
            ("rho", "Wasserstein perturbation radius", "robustness parameter"),
        ],
        domain_problem="Accessibility rankings can depend sharply on uncertain destination mass.",
        central_tension=(
            "Nominal rankings are interpretable, but may be unstable under bounded distributional "
            "perturbations."
        ),
        mechanism=(
            "A Wasserstein ambiguity set perturbs destination attractiveness or regional demand "
            "and induces lower accessibility envelopes and rank changes."
        ),
        dgp=(
            "Generate synthetic regions, distances, destination weights, and bounded mass "
            "perturbations at fixed Wasserstein radii."
        ),
        baseline="nominal accessibility ranking",
        method="Wasserstein-perturbed accessibility envelope",
        hypothesis=(
            "The robust envelope identifies synthetic regions whose accessibility rank is "
            "sensitive to bounded destination-mass perturbations."
        ),
        metrics=["rank_stability", "worst_case_rank_drop", "accessibility_premium"],
        ablation="Vary Wasserstein radius and distance impedance gamma.",
        limitations=[
            "Synthetic accessibility weights do not establish real regional access patterns.",
            "The ambiguity radius is a design parameter, not an estimated real-world uncertainty.",
        ],
        failure_modes=[
            "Rankings remain unchanged across all perturbation radii.",
            "The robust envelope is dominated by arbitrary scale choices.",
        ],
    ),
    "matrix_factorization": _Template(
        title="Low-Rank Residual Structure in Synthetic OD-Flow Heterogeneity",
        model_type="low_rank_od_residual",
        equations=[
            "R_ij = log(F_ij+c) - log(F_hat_gravity_ij+c)",
            "R ≈ U_k S_k V_k^T",
        ],
        variables=[
            ("F_ij", "synthetic OD flow", "response"),
            ("F_hat_gravity_ij", "pooled gravity prediction", "baseline"),
            ("R_ij", "log-flow residual", "residual"),
            ("U_k,S_k,V_k", "rank-k residual factors", "representation"),
            ("c", "positive log offset", "constant"),
        ],
        domain_problem="Pooled gravity models can leave structured OD-flow residuals.",
        central_tension=(
            "Low-rank correction can recover latent structure but may compress noise without "
            "interpretable heterogeneity."
        ),
        mechanism="Rank-k factors represent latent origin and destination residual axes.",
        dgp=(
            "Generate gravity flows plus known low-rank origin/destination factors and noise; "
            "hold out OD pairs for evaluation."
        ),
        baseline="pooled gravity residuals without low-rank correction",
        method="rank-k residual correction",
        hypothesis=(
            "A rank-k residual correction improves held-out synthetic OD reconstruction over "
            "unfactorized pooled-gravity residuals."
        ),
        metrics=["held_out_MAE", "held_out_RMSE", "explained_residual_variance"],
        ablation="Vary latent rank, factor strength, and noise level.",
        limitations=[
            "Latent factors are identifiable only up to rotation and sign.",
            "Synthetic residual recovery does not establish real mobility mechanisms.",
        ],
        failure_modes=[
            "Latent structure is too weak relative to noise.",
            "Rank selection overfits held-out OD pairs.",
        ],
    ),
    "graph_curvature": _Template(
        title="Curvature-Based Bottleneck Diagnostics in Synthetic Mobility Networks",
        model_type="mobility_graph_curvature",
        equations=["G = (V,E,w)", "kappa_ij = curvature(i,j; G,w)"],
        variables=[
            ("G", "weighted OD mobility graph", "graph"),
            ("V", "synthetic regions", "nodes"),
            ("E", "mobility links", "edges"),
            ("w_ij", "OD-flow edge weight", "weight"),
            ("kappa_ij", "edge curvature diagnostic", "diagnostic"),
        ],
        domain_problem="Mobility bottlenecks may not coincide with low degree or low flow.",
        central_tension=(
            "Curvature-style geometry may expose bridges, but its signal can be sensitive to "
            "edge weights and graph construction."
        ),
        mechanism="Negative or extreme edge curvature marks synthetic bridge-like bottlenecks.",
        dgp="Generate weighted block mobility graphs with planted inter-community bottlenecks.",
        baseline="degree/strength centrality",
        method="curvature-style edge bottleneck diagnostic",
        hypothesis=(
            "Curvature diagnostics detect planted synthetic mobility bottlenecks missed by "
            "degree or strength centrality."
        ),
        metrics=["bottleneck_precision", "bottleneck_recall", "bottleneck_F1"],
        ablation="Vary bridge weight, edge noise, and community density.",
        limitations=[
            "Synthetic graph bottlenecks do not establish segregation or causal barriers.",
            "Curvature definitions may produce different rankings on the same graph.",
        ],
        failure_modes=[
            "Curvature does not separate planted bridges from ordinary edges.",
            "Degree or strength centrality performs equally well or better.",
        ],
    ),
    "topological_data_analysis": _Template(
        title="Persistent Accessibility Structure Under Boundary Perturbation",
        model_type="persistent_accessibility_filtration",
        equations=["K_tau = {i : a_i >= tau}", "D = PH({K_tau}_tau)"],
        variables=[
            ("a_i", "regional accessibility", "field"),
            ("tau", "accessibility threshold", "filtration parameter"),
            ("K_tau", "accessibility level set or threshold graph", "filtration"),
            ("D", "persistence diagram", "topological summary"),
        ],
        domain_problem="Accessibility clusters can change abruptly under regional boundaries.",
        central_tension=(
            "Persistent summaries retain multi-scale structure, while cluster labels are easier "
            "to interpret but partition-sensitive."
        ),
        mechanism="A filtration tracks connected accessibility structure across thresholds.",
        dgp=(
            "Generate synthetic accessibility fields, construct fine and perturbed regional "
            "partitions, and compute threshold filtrations."
        ),
        baseline="standard clustering/community detection",
        method="persistent accessibility summary",
        hypothesis=(
            "Persistence summaries are more stable than cluster labels under bounded synthetic "
            "boundary perturbations."
        ),
        metrics=["bottleneck_distance", "persistence_stability", "cluster_instability"],
        ablation="Vary boundary perturbation strength and accessibility noise.",
        limitations=[
            "Topological stability does not prove substantive geographic meaning.",
            "Filtration choices can dominate persistence summaries.",
        ],
        failure_modes=[
            "Persistence changes as much as cluster labels under perturbation.",
            "No persistent features survive synthetic noise.",
        ],
    ),
    "agent_based_modeling": _Template(
        title="Emergent Distance Decay from Heterogeneous Agent Accessibility Rules",
        model_type="heterogeneous_agent_destination_choice",
        equations=["P(i -> j) proportional to B_j exp(-alpha_i d_ij)"],
        variables=[
            ("P(i -> j)", "agent destination-choice probability", "probability"),
            ("B_j", "destination attractiveness", "weight"),
            ("alpha_i", "agent or origin distance sensitivity", "parameter"),
            ("d_ij", "origin-destination distance", "distance"),
        ],
        domain_problem="Aggregate gravity-like flows may emerge from heterogeneous micro-rules.",
        central_tension=(
            "A pooled flow law is parsimonious, but can hide heterogeneous agent preferences."
        ),
        mechanism=(
            "Agents choose destinations from attractiveness and heterogeneous distance "
            "sensitivity; choices aggregate into an OD matrix."
        ),
        dgp="Simulate fixed populations of agents, destinations, and heterogeneous alpha_i values.",
        baseline="pooled gravity model",
        method="heterogeneous agent destination-choice model",
        hypothesis=(
            "Heterogeneous accessibility rules aggregate into gravity-like synthetic flows only "
            "within bounded preference regimes."
        ),
        metrics=["aggregate_flow_fit", "heterogeneity_recovery", "failure_regime_rate"],
        ablation="Vary preference heterogeneity, destination concentration, and agent count.",
        limitations=[
            "Synthetic agents do not represent actual mobility behavior.",
            "Aggregate fit cannot identify individual decision mechanisms uniquely.",
        ],
        failure_modes=[
            "Aggregate flows are not gravity-like.",
            "Distinct micro-rules produce indistinguishable aggregate OD matrices.",
        ],
    ),
    "spatial_statistics": _Template(
        title="Spatial Autocorrelation Diagnostics for Gravity Residual Misspecification",
        model_type="gravity_residual_spatial_autocorrelation",
        equations=["e = y - y_hat_gravity", "I(e,W) = Moran(e; W)"],
        variables=[
            ("e_i or e_ij", "gravity residual field", "residual"),
            ("W", "spatial weights matrix", "dependence structure"),
            ("I", "Moran-style statistic", "diagnostic"),
        ],
        domain_problem=(
            "Missing regional heterogeneity can remain spatially structured in residuals."
        ),
        central_tension=(
            "Global fit metrics summarize error, while spatial diagnostics may detect structure "
            "but risk false positives under misspecified weights."
        ),
        mechanism=(
            "Spatial weights aggregate neighboring residual cross-products into a diagnostic."
        ),
        dgp=(
            "Generate gravity flows with and without regional heterogeneity, fit a pooled model, "
            "and compute residual statistics under fixed spatial weights."
        ),
        baseline="unstructured residual diagnostics",
        method="Moran-style gravity residual diagnostic",
        hypothesis=(
            "Residual spatial autocorrelation detects configured synthetic regional heterogeneity "
            "while controlling false positives under the null DGP."
        ),
        metrics=["moran_statistic", "detection_power", "false_positive_rate"],
        ablation="Vary heterogeneity strength, spatial range, and weights misspecification.",
        limitations=[
            "Detection is conditional on the chosen spatial weights matrix.",
            "Residual autocorrelation does not identify a causal mechanism.",
        ],
        failure_modes=[
            "False-positive control fails under the null DGP.",
            "Power remains low under meaningful synthetic heterogeneity.",
        ],
    ),
    "network_science": _Template(
        title="Boundary Stability of Mobility Communities in Synthetic OD Networks",
        model_type="mobility_community_boundary_stability",
        equations=["G_OD = (V,E,F)", "Pi_s = Community(G_OD under partition s)"],
        variables=[
            ("G_OD", "weighted OD-flow network", "graph"),
            ("F", "OD-flow weights", "edge weights"),
            ("Pi_s", "community partition at boundary scale s", "partition"),
            ("s", "aggregation or perturbation scale", "stress parameter"),
        ],
        domain_problem="Mobility communities can be artifacts of regional aggregation.",
        central_tension=(
            "Community partitions summarize flow structure, but can be unstable under boundary "
            "changes."
        ),
        mechanism=(
            "Community detection is repeated after controlled node aggregation or relabeling."
        ),
        dgp="Generate block-structured OD networks and deterministic boundary perturbations.",
        baseline="gravity residual clustering",
        method="OD-flow mobility community detection",
        hypothesis=(
            "Mobility communities retain higher partition stability than gravity-residual clusters "
            "under configured synthetic boundary perturbations."
        ),
        metrics=["partition_stability", "adjusted_mutual_information", "robustness_ratio"],
        ablation="Vary aggregation scale, edge noise, and planted community strength.",
        limitations=[
            "Synthetic communities do not establish real functional regions.",
            "Community results depend on algorithm resolution and graph construction.",
        ],
        failure_modes=[
            "Partitions collapse under mild aggregation.",
            "Gravity residual clusters are equally or more stable.",
        ],
    ),
    "kernel_methods": _Template(
        title="Kernelized Spatial Interaction Under Nonmonotone Synthetic Regional Affinity",
        model_type="kernelized_spatial_interaction",
        equations=["F_ij = A_i B_j K_theta(x_i,x_j) exp(epsilon_ij)"],
        variables=[
            ("F_ij", "synthetic OD flow", "response"),
            ("A_i", "origin mass", "covariate"),
            ("B_j", "destination attractiveness", "covariate"),
            ("K_theta", "spatial affinity kernel", "interaction function"),
            ("x_i,x_j", "regional features or coordinates", "inputs"),
        ],
        domain_problem="Regional affinity may be nonmonotone in distance or observed features.",
        central_tension=(
            "A monotone gravity law is interpretable, while a kernel captures nonlinear affinity "
            "at higher complexity."
        ),
        mechanism="The kernel maps regional coordinates/features to a bounded nonlinear affinity.",
        dgp=(
            "Generate masses, coordinates, nonmonotone affinity regimes, and noisy OD flows with "
            "fixed seeds."
        ),
        baseline="monotone distance-decay gravity",
        method="kernelized spatial interaction model",
        hypothesis=(
            "A kernel interaction model improves held-out synthetic OD error and recovers "
            "configured nonmonotone affinity missed by monotone distance decay."
        ),
        metrics=["held_out_MAE", "held_out_RMSE", "affinity_recovery"],
        ablation="Vary kernel scale, nonmonotonicity strength, and noise.",
        limitations=[
            "Kernel fit does not establish a causal or interpretable spatial mechanism.",
            "Synthetic affinity recovery does not imply real-world predictive validity.",
        ],
        failure_modes=[
            "The kernel overfits and does not improve held-out error.",
            "Recovered affinity is unstable across kernel scales.",
        ],
    ),
}


def _verification_feasibility(candidate: VarianceAugmentedCandidate) -> float:
    text = (
        f"{candidate.method_lens} {candidate.verification_path} "
        f"{candidate.experiment_or_proof_plan} "
        f"{candidate.data_regime}"
    ).lower()
    if "private data" in text or any(
        phrase in text
        for phrase in ("requires network", "network access", "external retrieval")
    ):
        return 0.45
    if "synthetic" in text or "deterministic" in text or "held-out" in text:
        return 0.95
    return 0.70


def _semantic_fingerprint(candidate: VarianceAugmentedCandidate) -> str:
    return "|".join(
        [
            _normalize(candidate.title),
            _normalize(candidate.theory_object),
            _normalize(candidate.baseline),
        ]
    )


def _promotion_rank(candidate: SubstratePromotionCandidate) -> tuple[float, str]:
    return (-candidate.total_score, candidate.candidate_id)


def _load_latest_variance_report(
    reports: Path,
) -> tuple[Path, VarianceAugmentationReport]:
    generated_path = _latest_matching(reports, _VARIANCE_RE)
    application_path = _latest_matching(reports, _VARIANCE_APPLICATION_RE)
    path = generated_path
    if application_path is not None:
        try:
            application = VarianceAugmentationReport.model_validate_json(
                application_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise SubstratePromotionError(
                f"Could not load variance application: {exc}"
            ) from exc
        source_stem = (
            Path(application.source_augmentation_report_path_optional).stem
            if application.source_augmentation_report_path_optional
            else None
        )
        if generated_path is None or source_stem == generated_path.stem:
            path = application_path
    if path is None:
        raise SubstratePromotionError(
            "No variance augmentation report found. Run augment-variance first."
        )
    try:
        return path, VarianceAugmentationReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise SubstratePromotionError(f"Could not load variance report: {exc}") from exc


def _latest_matching(reports: Path, pattern: re.Pattern[str]) -> Path | None:
    matches = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("*.json")
        if (match := pattern.fullmatch(path.name))
    )
    return matches[-1][1] if matches else None


def _next_number(reports: Path, pattern: re.Pattern[str]) -> int:
    latest = _latest_matching(reports, pattern)
    if latest is None:
        return 1
    match = pattern.fullmatch(latest.name)
    return int(match.group(1)) + 1 if match else 1


def _substrate_artifact_id(
    build_number: int, index: int, substrate: ScientificSubstrate
) -> str:
    return f"scientific-substrate-{build_number:04d}-{index:02d}-{_slug(substrate.title)}"


def _normalize(value: str) -> str:
    return " ".join(sorted(_TOKEN_RE.findall(value.lower())))


def _slug(value: str) -> str:
    return "-".join(_TOKEN_RE.findall(value.lower())[:12]) or "item"


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "SubstratePromotionError",
    "SubstratePromotionResult",
    "inspect_substrate_promotion",
    "promote_variance_substrates",
    "render_substrate_promotion_markdown",
    "render_substrate_promotion_text",
]
