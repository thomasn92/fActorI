"""Non-fake LLM variance generation and deterministic IdeaTree construction."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.errors import AdapterError
from factori.adapters.llm_variance import VarianceGenerationClient
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AtlasScanReport,
    BackendKind,
    ControllerActionType,
    DeepOpportunityCandidate,
    DeepOpportunityDiscoveryReport,
    DomainMethodPair,
    IdeaEdge,
    IdeaNode,
    IdeaTreeConstructionReport,
    LLMVarianceBatch,
    LLMVarianceCandidate,
    LLMVarianceGenerationConfig,
    LLMVarianceGenerationInspectionReport,
    LLMVarianceGenerationReport,
    LLMVarianceRawArtifact,
    LLMVarianceScore,
    ProductionModePolicy,
    RetrievalContext,
    ScientificStageKind,
    StageBackendRecord,
)

_DEEP_RE = re.compile(r"^deep-opportunity-discovery-report-(\d{4})\.json$")
_ATLAS_SCAN_RE = re.compile(r"^atlas-scan-(\d{4})\.json$")
_VARIANCE_RE = re.compile(r"^llm-variance-generation-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-variance-raw-(\d{4})\.json$")
_TREE_RE = re.compile(r"^idea-tree-construction-report-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class LLMVarianceError(RuntimeError):
    """Raised when production-safe LLM variance cannot proceed."""


@dataclass(frozen=True)
class LLMVarianceResult:
    run_id: str
    report: LLMVarianceGenerationReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


@dataclass(frozen=True)
class IdeaTreeConstructionResult:
    run_id: str
    report: IdeaTreeConstructionReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def generate_llm_variance(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    generator: VarianceGenerationClient,
    config: LLMVarianceGenerationConfig,
) -> LLMVarianceResult:
    """Generate and select non-fake LLM variants from selected M98 opportunities."""
    if config.run_id != run_id:
        raise LLMVarianceError("LLM variance config run_id does not match run_id.")
    if generator.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise LLMVarianceError("LLM variance requires a recorded non-fake LLM backend.")
    if generator.fallback_used:
        raise LLMVarianceError("LLM variance forbids deterministic or fake fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    deep_path, deep = _load_latest_deep_report(run_id=run_id, reports=reports)
    if config.require_non_fake_backends and not deep.production_ready:
        raise LLMVarianceError(
            "Strict LLM variance requires a production-eligible deep opportunity report."
        )
    source_by_id = {item.opportunity_id: item for item in deep.candidates}
    source_candidates = [
        source_by_id[item]
        for item in deep.selected_opportunity_ids
        if item in source_by_id
    ][: config.max_source_opportunities]
    max_sources_by_total = config.max_variants_total // config.variants_per_opportunity
    source_candidates = source_candidates[:max_sources_by_total]
    if not source_candidates:
        raise LLMVarianceError("Deep opportunity report has no selected source opportunities.")
    if len(source_candidates) > config.max_generation_calls:
        raise LLMVarianceError(
            f"LLM variance requires {len(source_candidates)} calls, above "
            f"max_generation_calls={config.max_generation_calls}."
        )

    _, atlas_scan = _load_latest_atlas_scan(run_id=run_id, reports=reports)
    pair_by_id = {item.pair_id: item for item in atlas_scan.selected_pairs}
    retrieval_by_pair = _load_retrieval_contexts(root_path=root_path, deep=deep)
    report_number = _next_number(reports, _VARIANCE_RE)
    report_id = f"llm-variance-generation-report-{report_number:04d}"
    raw_number = _next_number(reports, _RAW_RE)
    candidates: list[LLMVarianceCandidate] = []
    scores: list[LLMVarianceScore] = []
    batches: list[LLMVarianceBatch] = []
    raw_artifacts: list[LLMVarianceRawArtifact] = []
    warnings: list[str] = []

    for source_index, source in enumerate(source_candidates):
        context = retrieval_by_pair.get(source.source_pair_id)
        if context is None:
            raise LLMVarianceError(
                f"Retrieval context is missing for source pair {source.source_pair_id}."
            )
        prompt_id = f"{report_id}-prompt-{source_index + 1:03d}"
        try:
            response = generator.generate_variants(
                prompt_id=prompt_id,
                source_payload=source.model_dump(mode="json"),
                retrieval_context_payload=context.model_dump(mode="json"),
                variants_per_opportunity=config.variants_per_opportunity,
            )
        except (AdapterError, ValueError) as exc:
            raise LLMVarianceError(
                f"LLM variance failed for {source.opportunity_id}: {exc}"
            ) from exc
        if not response.accepted:
            raise LLMVarianceError(
                f"LLM produced no valid variants for {source.opportunity_id}."
            )
        family_counts = Counter(item.candidate.variant_family for item in response.accepted)
        family_contract = bool(
            {"benchmark", "baseline_strengthening"}.intersection(family_counts)
            and {"robustness", "negative_control"}.intersection(family_counts)
        )
        if not family_contract:
            raise LLMVarianceError(
                f"LLM variants for {source.opportunity_id} do not satisfy required benchmark/"
                "baseline and robustness/negative-control family coverage."
            )
        accepted_ids: list[str] = []
        for item_index, item in enumerate(response.accepted, start=1):
            variant_id = (
                f"llm-variant-{report_number:04d}-{_slug(source.opportunity_id)}-"
                f"{item_index:02d}"
            )
            try:
                candidate = LLMVarianceCandidate(
                    variant_id=variant_id,
                    run_id=run_id,
                    source_opportunity_id=source.opportunity_id,
                    source_pair_id=source.source_pair_id,
                    domain_id=source.domain_id,
                    method_id=source.method_id,
                    **item.candidate.model_dump(mode="python"),
                )
            except ValidationError as exc:
                response.rejected.append(
                    {"index": item_index - 1, "reasons": [str(exc)]}
                )
                continue
            score = LLMVarianceScore(
                variant_id=variant_id,
                **item.score.model_dump(mode="python"),
            )
            candidates.append(candidate)
            scores.append(score)
            accepted_ids.append(variant_id)
        if not accepted_ids:
            raise LLMVarianceError(
                f"No schema-valid variants remained for {source.opportunity_id}."
            )
        raw_id = f"llm-variance-raw-{raw_number + source_index:04d}"
        raw_artifacts.append(
            LLMVarianceRawArtifact(
                raw_artifact_id=raw_id,
                run_id=run_id,
                source_opportunity_id=source.opportunity_id,
                backend_name=generator.backend_name,
                model=generator.model,
                prompt=response.prompt,
                raw_response=response.raw_response,
                accepted_variant_ids=accepted_ids,
                rejected_outputs=response.rejected,
                fallback_used=generator.fallback_used,
            )
        )
        batches.append(
            LLMVarianceBatch(
                batch_id=f"{report_id}-batch-{source_index + 1:03d}",
                source_opportunity_id=source.opportunity_id,
                prompt=response.prompt,
                generated_variant_ids=accepted_ids,
                rejected_outputs=response.rejected,
                family_counts=dict(sorted(family_counts.items())),
                required_family_contract_passed=True,
            )
        )
        if response.rejected:
            warnings.append(
                f"Rejected {len(response.rejected)} malformed or unsafe variants for "
                f"{source.opportunity_id}."
            )

    selected, _, duplicate_count, source_repeat_count = select_llm_variants(
        candidates=candidates,
        scores=scores,
        source_candidates=source_candidates,
        pairs=list(pair_by_id.values()),
        max_selected=config.max_selected_variants,
        min_variant_families=config.min_variant_family_coverage,
        min_domain_families=config.min_domain_family_coverage,
        min_method_families=config.min_method_family_coverage,
        suppress_duplicates=config.near_duplicate_suppression,
    )
    if not selected:
        raise LLMVarianceError("No variants remained after source-repeat and diversity filtering.")
    selected_ids = {item.variant_id for item in selected}
    candidates = [
        item.model_copy(update={"selected_for_tree": item.variant_id in selected_ids})
        for item in candidates
    ]
    if duplicate_count:
        warnings.append(f"Suppressed {duplicate_count} near-duplicate variants.")
    if source_repeat_count:
        warnings.append(f"Suppressed {source_repeat_count} source-opportunity repeats.")

    generation_record = _generation_backend_record(
        report_id=report_id,
        generator=generator,
        raw_ids=[item.raw_artifact_id for item in raw_artifacts],
    )
    selector_record = _selector_backend_record(report_id)
    backend_records = [generation_record, selector_record]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*deep.backend_records, *backend_records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.OPPORTUNITY_DISCOVERY,
            ScientificStageKind.LITERATURE_RETRIEVAL,
            ScientificStageKind.VARIANCE_GENERATION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        details = "; ".join(item.message for item in production.violations)
        raise LLMVarianceError(f"Strict production-mode LLM variance blocked: {details}")

    selected_pairs = [pair_by_id[item.source_pair_id] for item in selected]
    family_coverage = len({item.variant_family for item in selected})
    domain_coverage = len({item.domain_family for item in selected_pairs})
    method_coverage = len({item.method_family for item in selected_pairs})
    if family_coverage < min(config.min_variant_family_coverage, 7):
        warnings.append("Selected variants did not reach requested family coverage.")
    report = LLMVarianceGenerationReport(
        run_id=run_id,
        report_id=report_id,
        generation_status="completed_with_warnings" if warnings else "completed",
        config=config,
        source_deep_opportunity_report_path=_relative(root_path, deep_path),
        source_opportunity_count=len(source_candidates),
        generated_variant_count=len(candidates),
        rejected_variant_count=sum(len(item.rejected_outputs) for item in raw_artifacts),
        selected_variant_count=len(selected),
        variant_family_coverage=family_coverage,
        domain_family_coverage=domain_coverage,
        method_family_coverage=method_coverage,
        near_duplicate_suppressed_count=duplicate_count,
        source_repeat_suppressed_count=source_repeat_count,
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        batches=batches,
        candidates=candidates,
        scores=scores,
        selected_variant_ids=[item.variant_id for item in selected],
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    persistence = _persist_variance_report(
        report=report,
        raw_artifacts=raw_artifacts,
        store=store,
        ledger=ledger,
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return LLMVarianceResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
    )


def select_llm_variants(
    *,
    candidates: list[LLMVarianceCandidate],
    scores: list[LLMVarianceScore],
    source_candidates: list[DeepOpportunityCandidate],
    pairs: list[DomainMethodPair],
    max_selected: int,
    min_variant_families: int,
    min_domain_families: int,
    min_method_families: int,
    suppress_duplicates: bool = True,
) -> tuple[list[LLMVarianceCandidate], list[LLMVarianceScore], int, int]:
    """Select LLM-scored variants with source-repeat, duplicate, and coverage constraints."""
    candidate_by_id = {item.variant_id: item for item in candidates}
    source_by_id = {item.opportunity_id: item for item in source_candidates}
    pair_by_id = {item.pair_id: item for item in pairs}
    ordered = sorted(scores, key=lambda item: (-item.final_score, item.variant_id))
    unique: list[LLMVarianceScore] = []
    seen: set[str] = set()
    duplicate_count = 0
    source_repeat_count = 0
    for score in ordered:
        candidate = candidate_by_id.get(score.variant_id)
        if candidate is None or candidate.source_pair_id not in pair_by_id:
            continue
        source = source_by_id.get(candidate.source_opportunity_id)
        if source is None:
            continue
        if _is_source_repeat(candidate, source):
            source_repeat_count += 1
            continue
        fingerprint = _variant_fingerprint(candidate)
        if suppress_duplicates and fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        unique.append(score)

    selected: list[LLMVarianceScore] = []
    family_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()

    def add(score: LLMVarianceScore) -> None:
        candidate = candidate_by_id[score.variant_id]
        pair = pair_by_id[candidate.source_pair_id]
        selected.append(score)
        family_counts[candidate.variant_family] += 1
        domain_counts[pair.domain_family] += 1
        method_counts[pair.method_family] += 1

    for score in unique:
        if len(family_counts) >= min_variant_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.variant_id]
        if candidate.variant_family not in family_counts:
            add(score)
    for score in unique:
        if len(domain_counts) >= min_domain_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.variant_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.domain_family not in domain_counts and score not in selected:
            add(score)
    for score in unique:
        if len(method_counts) >= min_method_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.variant_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.method_family not in method_counts and score not in selected:
            add(score)
    for score in unique:
        if len(selected) >= max_selected:
            break
        if score not in selected:
            add(score)
    return (
        [candidate_by_id[item.variant_id] for item in selected],
        selected,
        duplicate_count,
        source_repeat_count,
    )


def inspect_llm_variance(
    *, run_id: str, root: str | Path = "."
) -> LLMVarianceGenerationInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _VARIANCE_RE)
    if path is None:
        return LLMVarianceGenerationInspectionReport(run_id=run_id, llm_variance_present=False)
    report = _load_variance_report(path)
    candidate_by_id = {item.variant_id: item for item in report.candidates}
    score_by_id = {item.variant_id: item for item in report.scores}
    return LLMVarianceGenerationInspectionReport(
        run_id=run_id,
        llm_variance_present=True,
        latest_report_id_optional=report.report_id,
        generation_status_optional=report.generation_status,
        source_opportunity_count=report.source_opportunity_count,
        generated_variant_count=report.generated_variant_count,
        rejected_variant_count=report.rejected_variant_count,
        selected_variant_count=report.selected_variant_count,
        variant_family_coverage=report.variant_family_coverage,
        domain_family_coverage=report.domain_family_coverage,
        method_family_coverage=report.method_family_coverage,
        near_duplicate_suppressed_count=report.near_duplicate_suppressed_count,
        source_repeat_suppressed_count=report.source_repeat_suppressed_count,
        selected_variants=[
            candidate_by_id[item]
            for item in report.selected_variant_ids
            if item in candidate_by_id
        ],
        selected_scores=[
            score_by_id[item] for item in report.selected_variant_ids if item in score_by_id
        ],
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def construct_idea_tree_from_llm_variance(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> IdeaTreeConstructionResult:
    """Persist deterministic tree nodes/edges from non-fake LLM-authored scientific content."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    variance_path = _latest_matching(reports, _VARIANCE_RE)
    if variance_path is None:
        raise LLMVarianceError("No LLM variance report found; run generate-llm-variance first.")
    variance = _load_variance_report(variance_path)
    deep_path = root_path / variance.source_deep_opportunity_report_path
    try:
        deep = DeepOpportunityDiscoveryReport.model_validate_json(
            deep_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMVarianceError(f"Could not load source deep opportunity report: {exc}") from exc
    generation_records = [
        item
        for item in variance.backend_records
        if item.stage_kind == ScientificStageKind.VARIANCE_GENERATION
    ]
    if not generation_records or any(
        item.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}
        or not item.allowed_in_production
        or item.fallback_used
        for item in generation_records
    ):
        raise LLMVarianceError(
            "IdeaTree construction requires non-fake, no-fallback LLM variance provenance."
        )
    generation_backend_kind = generation_records[0].backend_kind

    selected_by_id = {
        item.variant_id: item
        for item in variance.candidates
        if item.variant_id in set(variance.selected_variant_ids)
    }
    source_by_id = {item.opportunity_id: item for item in deep.candidates}
    retrieval_by_pair = _load_retrieval_contexts(root_path=root_path, deep=deep)
    source_ids = sorted({item.source_opportunity_id for item in selected_by_id.values()})
    report_number = _next_number(reports, _TREE_RE)
    report_id = f"idea-tree-construction-report-{report_number:04d}"
    report_path = f"runs/{run_id}/reports/{report_id}.json"
    nodes: list[IdeaNode] = []
    edges: list[IdeaEdge] = []
    for source_id in source_ids:
        source = source_by_id.get(source_id)
        if source is None:
            raise LLMVarianceError(f"Source opportunity {source_id} is missing.")
        context = retrieval_by_pair.get(source.source_pair_id)
        nodes.append(
            IdeaNode(
                node_id=source.opportunity_id,
                parent_id_optional="idea-root",
                depth=1,
                stage_origin="deep_opportunity",
                title=f"{source.domain_name} x {source.method_name}",
                domain=source.domain_name,
                method_optional=source.method_name,
                research_question_optional=source.research_question,
                hypothesis_optional=source.hypothesis,
                model_hint_optional=source.theory_or_model_object,
                experiment_hint_optional=source.experiment_or_proof_plan,
                baseline_hint_optional="; ".join(source.baseline_candidates),
                data_regime_optional=source.data_regime,
                status="expanded",
                survivor_reason_optional="selected M98 deep opportunity with LLM variants",
                source_opportunity_id_optional=source.opportunity_id,
                source_pair_id_optional=source.source_pair_id,
                source_method_lens_id_optional=source.method_id,
                backend_kind_optional=generation_backend_kind,
                retrieval_context_id_optional=context.context_id if context else None,
                artifact_refs=sorted(
                    {
                        variance.source_deep_opportunity_report_path,
                        report_path,
                        *(deep.retrieval_context_paths),
                    }
                ),
            )
        )
        edges.append(
            IdeaEdge(
                edge_id=f"edge-{len(edges) + 1:04d}",
                source_node_id="idea-root",
                target_node_id=source.opportunity_id,
                edge_type="root_to_deep_opportunity",
                rationale="selected retrieval-contextualized M98 opportunity",
            )
        )
    for variant in sorted(selected_by_id.values(), key=lambda item: item.variant_id):
        source = source_by_id[variant.source_opportunity_id]
        context = retrieval_by_pair.get(variant.source_pair_id)
        nodes.append(
            IdeaNode(
                node_id=variant.variant_id,
                parent_id_optional=variant.source_opportunity_id,
                depth=2,
                stage_origin="llm_variance",
                title=variant.title,
                domain=source.domain_name,
                method_optional=source.method_name,
                research_question_optional=variant.research_question,
                hypothesis_optional=variant.hypothesis,
                model_hint_optional=variant.theory_or_model_object,
                experiment_hint_optional=variant.experiment_or_proof_plan,
                baseline_hint_optional="; ".join(variant.baseline_candidates),
                data_regime_optional=variant.data_regime,
                status="selected",
                survivor_reason_optional=variant.scientific_rationale,
                source_opportunity_id_optional=variant.source_opportunity_id,
                source_pair_id_optional=variant.source_pair_id,
                source_method_lens_id_optional=variant.method_id,
                variant_family_optional=variant.variant_family,
                backend_kind_optional=generation_backend_kind,
                retrieval_context_id_optional=context.context_id if context else None,
                artifact_refs=sorted(
                    {
                        _relative(root_path, variance_path),
                        report_path,
                    }
                ),
            )
        )
        edges.append(
            IdeaEdge(
                edge_id=f"edge-{len(edges) + 1:04d}",
                source_node_id=variant.source_opportunity_id,
                target_node_id=variant.variant_id,
                edge_type="deep_opportunity_to_variant",
                mutation_operator_optional=variant.variant_family,
                rationale=variant.scientific_rationale,
            )
        )
    construction_record = _construction_backend_record(
        report_id=report_id,
        artifact_ids=[report_id, *[item.node_id for item in nodes]],
    )
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*variance.backend_records, construction_record],
        policy=ProductionModePolicy(require_non_fake_backends=True),
        expected_stage_kinds=[
            ScientificStageKind.VARIANCE_GENERATION,
            ScientificStageKind.IDEA_TREE_CONSTRUCTION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if production.blocking_violation_count:
        details = "; ".join(item.message for item in production.violations)
        raise LLMVarianceError(f"IdeaTree construction production check blocked: {details}")
    report = IdeaTreeConstructionReport(
        run_id=run_id,
        report_id=report_id,
        construction_status="completed",
        source_variance_report_path=_relative(root_path, variance_path),
        source_deep_opportunity_report_path=_relative(root_path, deep_path),
        parent_opportunity_node_count=len(source_ids),
        variant_node_count=len(selected_by_id),
        idea_tree_nodes_added=len(nodes),
        idea_tree_edges_added=len(edges),
        nodes=nodes,
        edges=edges,
        backend_records=[construction_record],
        production_ready=variance.production_ready and not production.blocking_violation_count,
    )
    metadata = _metadata("llm_idea_tree_construction")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_idea_tree_construction_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ],
        action_type=ControllerActionType.LLM_IDEA_TREE_CONSTRUCTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "report_id": report_id,
            "idea_tree_nodes_added": len(nodes),
            "idea_tree_edges_added": len(edges),
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return IdeaTreeConstructionResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
    )


def render_llm_variance_text(report: LLMVarianceGenerationInspectionReport) -> str:
    return "\n".join(
        [
            f"LLM variance: {'present' if report.llm_variance_present else 'absent'}",
            f"Status: {report.generation_status_optional or 'not available'}",
            f"Source opportunities: {report.source_opportunity_count}",
            f"Generated variants: {report.generated_variant_count}",
            f"Selected variants: {report.selected_variant_count}",
            f"Variant-family coverage: {report.variant_family_coverage}",
            f"Domain/method family coverage: {report.domain_family_coverage}/"
            f"{report.method_family_coverage}",
            f"Near duplicates suppressed: {report.near_duplicate_suppressed_count}",
            f"Source repeats suppressed: {report.source_repeat_suppressed_count}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_llm_variance_markdown(report: LLMVarianceGenerationReport) -> str:
    candidate_by_id = {item.variant_id: item for item in report.candidates}
    lines = [
        "# LLM Variance Generation",
        "",
        f"Status: `{report.generation_status}`",
        f"Source opportunities: `{report.source_opportunity_count}`",
        f"Generated variants: `{report.generated_variant_count}`",
        f"Selected variants: `{report.selected_variant_count}`",
        f"Variant-family coverage: `{report.variant_family_coverage}`",
        "",
        "## Selected Variants",
        "",
    ]
    lines.extend(
        f"- **{candidate_by_id[item].title}** "
        f"(`{candidate_by_id[item].variant_family}`)"
        for item in report.selected_variant_ids
        if item in candidate_by_id
    )
    lines.extend(
        [
            "",
            "Variants are LLM-authored planning context. They do not create verification "
            "evidence, scientific validation, or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_idea_tree_construction_markdown(report: IdeaTreeConstructionReport) -> str:
    return "\n".join(
        [
            "# LLM Variance IdeaTree Construction",
            "",
            f"Parent opportunity nodes: `{report.parent_opportunity_node_count}`",
            f"Variant nodes: `{report.variant_node_count}`",
            f"Nodes added: `{report.idea_tree_nodes_added}`",
            f"Edges added: `{report.idea_tree_edges_added}`",
            "",
            "Tree construction is deterministic provenance infrastructure over non-fake "
            "LLM-authored content. It creates no evidence or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )


def _persist_variance_report(
    *,
    report: LLMVarianceGenerationReport,
    raw_artifacts: list[LLMVarianceRawArtifact],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("llm_variance_generation")
    specs = [
        ArtifactWriteSpec(
            item.raw_artifact_id,
            ArtifactType.REPORT,
            item,
            "json",
            _metadata("llm_variance_raw"),
        )
        for item in raw_artifacts
    ]
    specs.extend(
        [
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_llm_variance_markdown(report),
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
        action_type=ControllerActionType.LLM_VARIANCE_GENERATION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "source_opportunity_count": report.source_opportunity_count,
            "generated_variant_count": report.generated_variant_count,
            "selected_variant_count": report.selected_variant_count,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )


def _generation_backend_record(
    *, report_id: str, generator: VarianceGenerationClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-llm-generation",
        stage_kind=ScientificStageKind.VARIANCE_GENERATION,
        backend_kind=generator.backend_kind,
        backend_name=generator.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason=(
            "Scientific variant questions, hypotheses, objects, designs, baselines, controls, "
            "rationales, and scores come from the recorded non-fake LLM backend."
        ),
        artifact_ids=[report_id, *raw_ids],
        fallback_used=generator.fallback_used,
        fallback_disclosed=generator.fallback_disclosed,
    )


def _selector_backend_record(report_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-diversity-selection",
        stage_kind=ScientificStageKind.DIVERSITY_SELECTION,
        backend_kind=BackendKind.HEURISTIC,
        backend_name="variance_coverage_and_dedup_selector",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Deterministic selection preserves LLM scores and enforces coverage/dedup only.",
        artifact_ids=[report_id],
    )


def _construction_backend_record(
    *, report_id: str, artifact_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=report_id,
        stage_kind=ScientificStageKind.IDEA_TREE_CONSTRUCTION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="deterministic_idea_tree_constructor",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Deterministic provenance construction over non-fake LLM-authored content only.",
        artifact_ids=artifact_ids,
    )


def _load_latest_deep_report(
    *, run_id: str, reports: Path
) -> tuple[Path, DeepOpportunityDiscoveryReport]:
    path = _latest_matching(reports, _DEEP_RE)
    if path is None:
        raise LLMVarianceError(
            f"No deep opportunity report found for run_id={run_id}; run M98 first."
        )
    try:
        report = DeepOpportunityDiscoveryReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMVarianceError(f"Could not load deep opportunity report: {exc}") from exc
    if report.run_id != run_id:
        raise LLMVarianceError("Deep opportunity report run_id is inconsistent.")
    return path, report


def _load_latest_atlas_scan(*, run_id: str, reports: Path) -> tuple[Path, AtlasScanReport]:
    path = _latest_matching(reports, _ATLAS_SCAN_RE)
    if path is None:
        raise LLMVarianceError("No atlas scan found for LLM variance family metadata.")
    try:
        report = AtlasScanReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMVarianceError(f"Could not load atlas scan: {exc}") from exc
    if report.run_id != run_id:
        raise LLMVarianceError("Atlas scan run_id is inconsistent.")
    return path, report


def _load_retrieval_contexts(
    *, root_path: Path, deep: DeepOpportunityDiscoveryReport
) -> dict[str, RetrievalContext]:
    contexts: dict[str, RetrievalContext] = {}
    for relative_path in deep.retrieval_context_paths:
        path = root_path / relative_path
        try:
            context = RetrievalContext.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise LLMVarianceError(f"Could not load retrieval context {path}: {exc}") from exc
        contexts[context.source_pair_id] = context
    return contexts


def _load_variance_report(path: Path) -> LLMVarianceGenerationReport:
    try:
        return LLMVarianceGenerationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMVarianceError(f"Could not load LLM variance report: {exc}") from exc


def _is_source_repeat(
    candidate: LLMVarianceCandidate, source: DeepOpportunityCandidate
) -> bool:
    source_tokens = set(
        _TOKEN_RE.findall(
            " ".join(
                [
                    source.research_question,
                    source.theory_or_model_object,
                    source.mathematical_or_computational_form,
                    source.experiment_or_proof_plan,
                ]
            ).lower()
        )
    )
    variant_tokens = set(
        _TOKEN_RE.findall(
            " ".join(
                [
                    candidate.research_question,
                    candidate.theory_or_model_object,
                    candidate.mathematical_or_computational_form,
                    candidate.experiment_or_proof_plan,
                ]
            ).lower()
        )
    )
    if not source_tokens or not variant_tokens:
        return False
    overlap = len(source_tokens & variant_tokens) / len(source_tokens | variant_tokens)
    return overlap >= 0.9


def _variant_fingerprint(candidate: LLMVarianceCandidate) -> str:
    tokens = _TOKEN_RE.findall(
        " ".join(
            [
                candidate.title,
                candidate.research_question,
                candidate.theory_or_model_object,
                candidate.mathematical_or_computational_form,
                " ".join(candidate.baseline_candidates),
            ]
        ).lower()
    )
    return " ".join(tokens)


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "llm_variance_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _slug(value: str) -> str:
    return "_".join(_TOKEN_RE.findall(value.lower()))


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    paths = [item for item in directory.iterdir() if pattern.match(item.name)]
    return max(paths, key=lambda item: item.name) if paths else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for item in directory.iterdir()
        if (match := pattern.match(item.name)) is not None
    ]
    return max(numbers, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "IdeaTreeConstructionResult",
    "LLMVarianceError",
    "LLMVarianceResult",
    "construct_idea_tree_from_llm_variance",
    "generate_llm_variance",
    "inspect_llm_variance",
    "render_idea_tree_construction_markdown",
    "render_llm_variance_markdown",
    "render_llm_variance_text",
    "select_llm_variants",
]
