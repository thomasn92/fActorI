"""Deterministic evidence-to-claim linkage maps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_text
from factori.ledger import ResearchLedger
from factori.persistence import (
    ArtifactWriteSpec,
    PersistenceResult,
    persist_artifacts_with_commit,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    Claim,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    ClaimTable,
    ControllerActionType,
    ExperimentArtifact,
    HumanReviewArtifact,
    ProofArtifact,
    RetrievalQualityReport,
)

_FORMAL_PROOF_TYPES = {"lean_verified", "formal_verified", "external_certificate"}
_INFORMAL_PROOF_TYPES = {"informal_proof_note", "proof_plan"}
_CITATION_CLAIM_CLASSES = {
    "literature_background_claim",
    "source_context_claim",
    "external_factual_claim",
}
_SCAFFOLD_OR_BOUNDARY_CLASSES = {
    "scaffold_statement",
    "problem_framing_statement",
    "method_description_statement",
    "evidence_boundary_statement",
    "limitation_statement",
    "provenance_statement",
}
BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID = "bounded-empirical-demonstration-claim"
BOUNDED_EMPIRICAL_DEMONSTRATION_SECTION = "Demonstration Status"
BOUNDED_EMPIRICAL_DEMONSTRATION_TEXT = (
    "The local synthetic calibration experiment reports metrics for the configured run only."
)
BOUNDED_EMPIRICAL_CLAIM_CLASSES = {
    "bounded_demonstration_claim",
    "bounded_empirical_result_claim",
    "synthetic_experiment_claim",
}
_EXPERIMENT_CLAIM_CLASSES = {
    "experiment_claim",
    "pipeline_status_claim",
    *BOUNDED_EMPIRICAL_CLAIM_CLASSES,
}
_FORBIDDEN_CLAIM_CLASSES = {"novelty_claim", "publication_readiness_claim"}


class ClaimEvidenceMapError(RuntimeError):
    """Raised when a claim-evidence map cannot be built or inspected."""


@dataclass(frozen=True)
class ClaimEvidenceMapPersistResult:
    """Persisted claim-evidence map and refreshed reviewer summary."""

    run_id: str
    claim_evidence_map: ClaimEvidenceMap
    persistence: PersistenceResult
    map_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    reviewer_summary_artifact: ArtifactRef
    reviewer_summary_markdown_artifact: ArtifactRef


def build_claim_evidence_map(
    *,
    run_id: str,
    root: str | Path = ".",
    claim_support_audit: ClaimSupportAuditReport | None = None,
    enable_empirical_demonstration_gaps: bool = False,
) -> ClaimEvidenceMap:
    """Build a deterministic final claim-evidence map without mutation."""
    root_path = Path(root)
    run_path = _require_run_path(root_path, run_id)
    reports = run_path / "reports"
    claim_support = claim_support_audit or _read_model(
        latest_claim_support_audit_path(root_path, run_id),
        ClaimSupportAuditReport,
    )
    if claim_support is None:
        raise ClaimEvidenceMapError(
            f"No claim-support audit found for run_id={run_id}."
        )
    citation_registry = _read_model(reports / "citation-registry.json", CitationRegistry)
    retrieval_quality = _read_model(
        reports / "retrieval-quality-report.json",
        RetrievalQualityReport,
    )
    claim_table = _read_model(reports / "claim-table.json", ClaimTable)
    proof_artifacts = _read_proof_artifacts(run_path)
    experiment_artifacts = _read_experiment_artifacts(run_path)
    human_review = _read_model(reports / "human-review-artifact.json", HumanReviewArtifact)

    links = [
        _link_from_claim_support_item(
            run_id=run_id,
            item=item,
            citation_registry=citation_registry,
            retrieval_quality=retrieval_quality,
            proof_artifacts=proof_artifacts,
            experiment_artifacts=experiment_artifacts,
            human_review=human_review,
        )
        for item in claim_support.claim_support_items
    ]
    if claim_table is not None:
        sentence_ids = {link.claim_id for link in links}
        links.extend(
            _link_from_claim_table_claim(
                run_id=run_id,
                claim=claim,
                proof_artifacts=proof_artifacts,
                experiment_artifacts=experiment_artifacts,
            )
            for claim in claim_table.claims
            if claim.claim_id not in sentence_ids
        )
    if _should_add_bounded_empirical_demonstration_link(
        links=links,
        experiment_artifacts=experiment_artifacts,
        enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
    ):
        links.append(
            _bounded_empirical_demonstration_link(
                run_id=run_id,
                experiment_artifacts=experiment_artifacts,
            )
        )
    links = sorted(links, key=lambda item: (item.section_name, item.claim_id))
    summary_counts = _summary_counts(links)
    unsupported_non_scaffold = [
        link.claim_id
        for link in links
        if link.support_status in {"unsupported", "blocked_forbidden_claim"}
        and link.requires_support
    ]
    return ClaimEvidenceMap(
        run_id=run_id,
        links=links,
        summary_counts=summary_counts,
        unsupported_non_scaffold_claim_ids=unsupported_non_scaffold,
        evidence_limitations=[
            "Citation links provide bounded background/source context only.",
            "Proof links are scoped to declared proof artifact claim IDs or hashes only.",
            (
                "Experiment links are scoped to declared experiment artifact claim IDs "
                "or sections only."
            ),
            "Human-review links record review occurrence or requested changes only.",
            (
                "Evidence linkage does not imply novelty, broad correctness, "
                "publication readiness, or scientific acceptance."
            ),
        ],
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def persist_claim_evidence_map(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    enable_empirical_demonstration_gaps: bool = False,
) -> ClaimEvidenceMapPersistResult:
    """Persist a claim-evidence map and refreshed reviewer summary."""
    root_path = Path(root)
    claim_map = build_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
    )
    markdown = render_claim_evidence_map_markdown(claim_map)
    map_id = _next_claim_evidence_map_id(root_path, run_id)
    reviewer_summary_id = _next_claim_evidence_reviewer_summary_id(root_path, run_id)

    # Local import avoids a module import cycle with full-paper inspection helpers.
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    reviewer_summary = build_reviewer_bundle_summary(
        run_id=run_id,
        root=root_path,
        claim_evidence_map=claim_map,
    )
    reviewer_summary_markdown = render_reviewer_bundle_summary_markdown(
        reviewer_summary
    )
    metadata = {
        "stage": "claim_evidence_mapping",
        "artifact_role": "claim_evidence_map_context",
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
                map_id,
                ArtifactType.REPORT,
                claim_map,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                f"{map_id}-markdown",
                ArtifactType.REPORT,
                markdown,
                "markdown",
                metadata,
                filename_stem=map_id,
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
        action_type=ControllerActionType.CLAIM_EVIDENCE_MAP_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "claim_evidence_supported_count": summary_count(
                claim_map,
                "supported_within_scope",
            ),
            "claim_evidence_partial_count": summary_count(
                claim_map,
                "partially_supported",
            ),
            "claim_evidence_unsupported_count": summary_count(
                claim_map,
                "unsupported",
            )
            + summary_count(claim_map, "blocked_forbidden_claim"),
            "publication_ready": False,
            "reviewer_summary_updated": True,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return ClaimEvidenceMapPersistResult(
        run_id=run_id,
        claim_evidence_map=claim_map,
        persistence=persistence,
        map_artifact=by_id[map_id],
        markdown_artifact=by_id[f"{map_id}-markdown"],
        reviewer_summary_artifact=by_id[reviewer_summary_id],
        reviewer_summary_markdown_artifact=by_id[f"{reviewer_summary_id}-markdown"],
    )


def inspect_claim_evidence_map(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect the latest persisted claim-evidence map without mutation."""
    root_path = Path(root)
    path = latest_claim_evidence_map_path(root_path, run_id)
    if path is None:
        raise ClaimEvidenceMapError(
            f"No claim-evidence map found for run_id={run_id}."
        )
    claim_map = ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    return {
        **claim_map.model_dump(mode="json"),
        **claim_evidence_summary_fields(claim_map),
        "claim_evidence_map_present": True,
        "claim_evidence_map_path": path.relative_to(root_path).as_posix(),
    }


def render_claim_evidence_map_markdown(claim_map: ClaimEvidenceMap) -> str:
    """Render a concise reviewer-facing claim-evidence map."""
    fields = claim_evidence_summary_fields(claim_map)
    lines = [
        "# Claim Evidence Map",
        "",
        f"Run ID: `{claim_map.run_id}`",
        f"Supported claims: `{fields['claim_evidence_supported_count']}`",
        f"Partially supported claims: `{fields['claim_evidence_partial_count']}`",
        f"Unsupported claims: `{fields['claim_evidence_unsupported_count']}`",
        f"Proof-supported claims: `{fields['proof_supported_claim_count']}`",
        f"Experiment-supported claims: `{fields['experiment_supported_claim_count']}`",
        f"Citation-supported claims: `{fields['citation_supported_claim_count']}`",
        f"Human-review-linked claims: `{fields['human_review_linked_claim_count']}`",
        "",
        "## Links",
    ]
    for link in claim_map.links:
        lines.extend(
            [
                f"- `{link.claim_id}`: `{link.support_status}` / `{link.support_type}`",
                f"  - section: `{link.section_name}`",
                f"  - class: `{link.claim_class}`",
                f"  - scope: {link.support_scope}",
            ]
        )
        if link.unsupported_reason:
            lines.append(f"  - unsupported reason: {link.unsupported_reason}")
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {item}" for item in claim_map.evidence_limitations)
    lines.extend(
        [
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def latest_claim_evidence_map_path(root: Path, run_id: str) -> Path | None:
    """Return the latest persisted claim-evidence map JSON path."""
    reports = root / "runs" / run_id / "reports"
    if not reports.is_dir():
        return None
    paths = [
        path
        for path in reports.glob("claim-evidence-map*.json")
        if not path.name.endswith(".meta.json")
    ]
    if not paths:
        return None
    return sorted(paths, key=_claim_evidence_map_sort_key)[-1]


def latest_claim_support_audit_path(root: Path, run_id: str) -> Path:
    """Return the latest final claim-support audit path for a run."""
    reports = root / "runs" / run_id / "reports"
    final_paths = sorted(
        path
        for path in reports.glob("claim-support-audit-after-final-manuscript-*.json")
        if not path.name.endswith(".meta.json")
    )
    if final_paths:
        return final_paths[-1]
    planned_spec_paths = sorted(
        path
        for path in reports.glob("claim-support-audit-after-planned-spec-execution-*.json")
        if not path.name.endswith(".meta.json")
    )
    if planned_spec_paths:
        return planned_spec_paths[-1]
    autonomous_paths = sorted(
        path
        for path in reports.glob("claim-support-audit-after-autonomous-execution-*.json")
        if not path.name.endswith(".meta.json")
    )
    if autonomous_paths:
        return autonomous_paths[-1]
    cycle_paths = sorted(
        path
        for path in reports.glob(
            "claim-support-audit-after-reconciliation-cycle-*.json"
        )
        if not path.name.endswith(".meta.json")
    )
    if cycle_paths:
        return cycle_paths[-1]
    reconciled = reports / "claim-support-audit-after-human-review-reconciliation.json"
    if reconciled.is_file():
        return reconciled
    refreshed = reports / "claim-support-audit-after-evidence-aware-refresh.json"
    if refreshed.is_file():
        return refreshed
    return reports / "claim-support-audit.json"


def claim_evidence_summary_fields(claim_map: ClaimEvidenceMap | None) -> dict[str, int | bool]:
    """Return stable summary fields for inspect/lint/reviewer summaries."""
    if claim_map is None:
        return {
            "claim_evidence_map_present": False,
            "claim_evidence_supported_count": 0,
            "claim_evidence_partial_count": 0,
            "claim_evidence_unsupported_count": 0,
            "proof_supported_claim_count": 0,
            "experiment_supported_claim_count": 0,
            "citation_supported_claim_count": 0,
            "human_review_linked_claim_count": 0,
        }
    return {
        "claim_evidence_map_present": True,
        "claim_evidence_supported_count": summary_count(
            claim_map,
            "supported_within_scope",
        ),
        "claim_evidence_partial_count": summary_count(claim_map, "partially_supported"),
        "claim_evidence_unsupported_count": (
            summary_count(claim_map, "unsupported")
            + summary_count(claim_map, "blocked_forbidden_claim")
        ),
        "proof_supported_claim_count": summary_count(
            claim_map,
            "proof_supported_claim",
        ),
        "experiment_supported_claim_count": summary_count(
            claim_map,
            "experiment_supported_claim",
        ),
        "citation_supported_claim_count": summary_count(
            claim_map,
            "citation_supported_background_claim",
        ),
        "human_review_linked_claim_count": summary_count(
            claim_map,
            "human_reviewed_claim",
        ),
    }


def summary_count(claim_map: ClaimEvidenceMap, key: str) -> int:
    """Return one summary count with integer normalization."""
    return int(claim_map.summary_counts.get(key, 0))


def _link_from_claim_support_item(
    *,
    run_id: str,
    item: ClaimSupportItem,
    citation_registry: CitationRegistry | None,
    retrieval_quality: RetrievalQualityReport | None,
    proof_artifacts: list[ProofArtifact],
    experiment_artifacts: list[ExperimentArtifact],
    human_review: HumanReviewArtifact | None,
) -> ClaimEvidenceMapLink:
    claim_id = item.sentence_id
    claim_hash = item.sentence_text_hash
    section_id = _slug(item.section_name)
    base = {
        "run_id": run_id,
        "claim_id": claim_id,
        "claim_text_hash": claim_hash,
        "section_name": item.section_name,
        "claim_class": item.claim_class,
        "supporting_citation_keys": list(item.citation_keys_present),
        "supporting_source_ids": list(item.supporting_source_ids),
        "evidence_limitations": _standard_limitations(),
    }
    if item.claim_class in _FORBIDDEN_CLAIM_CLASSES:
        return _make_link(
            **base,
            requires_support=True,
            support_status="blocked_forbidden_claim",
            classification="unsupported_claim",
            support_type="unsupported",
            support_scope="forbidden novelty or publication-readiness claim",
            unsupported_reason=(
                "novelty and publication-readiness claims cannot be supported by "
                "citations, proof artifacts, experiments, or human review in this policy"
            ),
        )
    if item.claim_class == "proof_claim":
        return _proof_link(
            base=base,
            proof_artifacts=proof_artifacts,
            match_tokens={claim_id, claim_hash},
            requires_support=True,
        )
    if item.claim_class in _EXPERIMENT_CLAIM_CLASSES:
        experiment_link = _experiment_link(
            base=base,
            experiment_artifacts=experiment_artifacts,
            match_tokens={claim_id, claim_hash, section_id},
            requires_support=item.claim_class == "experiment_claim",
        )
        if experiment_link is not None:
            return experiment_link
    human_link = _human_review_link(base=base, item=item, human_review=human_review)
    if human_link is not None:
        return human_link
    if (
        item.support_status == "not_required_scaffold"
        or item.claim_class in _SCAFFOLD_OR_BOUNDARY_CLASSES
    ):
        return _make_link(
            **base,
            requires_support=False,
            support_status="not_required_scaffold",
            classification="scaffold_or_boundary_statement",
            support_type="none_required",
            support_scope="scaffold, method, limitation, provenance, or evidence-boundary wording",
            unsupported_reason=None,
        )
    if item.claim_class in _CITATION_CLAIM_CLASSES:
        return _citation_link(
            base=base,
            item=item,
            citation_registry=citation_registry,
            retrieval_quality=retrieval_quality,
        )
    return _make_link(
        **base,
        requires_support=True,
        support_status="unsupported",
        classification="unsupported_claim",
        support_type="unsupported",
        support_scope="no compatible accepted evidence link was available",
        unsupported_reason=item.unsupported_reason or item.support_status,
    )


def _link_from_claim_table_claim(
    *,
    run_id: str,
    claim: Claim,
    proof_artifacts: list[ProofArtifact],
    experiment_artifacts: list[ExperimentArtifact],
) -> ClaimEvidenceMapLink:
    claim_hash = sha256_text(claim.claim_text)
    base = {
        "run_id": run_id,
        "claim_id": claim.claim_id,
        "claim_text_hash": claim_hash,
        "section_name": claim.allowed_section,
        "claim_class": _claim_class_for_label(claim),
        "supporting_citation_keys": [],
        "supporting_source_ids": [],
        "evidence_limitations": _standard_limitations(),
    }
    claim_text_key = claim.claim_text.casefold()
    if "novelty" in claim_text_key or "publication ready" in claim_text_key:
        return _make_link(
            **base,
            requires_support=True,
            support_status="blocked_forbidden_claim",
            classification="unsupported_claim",
            support_type="unsupported",
            support_scope="forbidden novelty or publication-readiness claim",
            unsupported_reason=(
                "proof and experiment artifacts cannot support novelty or "
                "publication readiness"
            ),
        )
    if base["claim_class"] == "proof_claim":
        return _proof_link(
            base=base,
            proof_artifacts=proof_artifacts,
            match_tokens={claim.claim_id, claim_hash},
            requires_support=True,
        )
    if base["claim_class"] == "experiment_claim":
        experiment_link = _experiment_link(
            base=base,
            experiment_artifacts=experiment_artifacts,
            match_tokens={claim.claim_id, claim_hash, _slug(claim.allowed_section)},
            requires_support=True,
        )
        if experiment_link is not None:
            return experiment_link
    return _make_link(
        **base,
        requires_support=True,
        support_status="unsupported",
        classification="unsupported_claim",
        support_type="unsupported",
        support_scope="claim table claim has no compatible linked proof or experiment artifact",
        unsupported_reason="no matching accepted evidence artifact",
    )


def _should_add_bounded_empirical_demonstration_link(
    *,
    links: list[ClaimEvidenceMapLink],
    experiment_artifacts: list[ExperimentArtifact],
    enable_empirical_demonstration_gaps: bool,
) -> bool:
    if any(link.claim_id == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID for link in links):
        return False
    if _bounded_empirical_artifact_present(experiment_artifacts):
        return True
    if not enable_empirical_demonstration_gaps:
        return False
    return not any(
        link.claim_class in _EXPERIMENT_CLAIM_CLASSES and link.requires_support
        for link in links
    )


def _bounded_empirical_artifact_present(
    experiment_artifacts: list[ExperimentArtifact],
) -> bool:
    claim_hash = sha256_text(BOUNDED_EMPIRICAL_DEMONSTRATION_TEXT)
    tokens = {
        BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
        claim_hash,
    }
    return any(
        tokens.intersection(artifact.claim_ids_or_section_ids)
        for artifact in experiment_artifacts
    )


def _bounded_empirical_demonstration_link(
    *,
    run_id: str,
    experiment_artifacts: list[ExperimentArtifact],
) -> ClaimEvidenceMapLink:
    claim_hash = sha256_text(BOUNDED_EMPIRICAL_DEMONSTRATION_TEXT)
    base = {
        "run_id": run_id,
        "claim_id": BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
        "claim_text_hash": claim_hash,
        "section_name": BOUNDED_EMPIRICAL_DEMONSTRATION_SECTION,
        "claim_class": "bounded_demonstration_claim",
        "supporting_citation_keys": [],
        "supporting_source_ids": [],
        "evidence_limitations": [
            *_standard_limitations(),
            (
                "The bounded empirical demonstration claim is synthetic/local and "
                "supports only metrics for the configured run."
            ),
        ],
    }
    experiment_link = _experiment_link(
        base=base,
        experiment_artifacts=experiment_artifacts,
        match_tokens={
            BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
            claim_hash,
        },
        requires_support=True,
    )
    if experiment_link is not None:
        return experiment_link
    return _make_link(
        **base,
        requires_support=True,
        support_status="unsupported",
        classification="unsupported_claim",
        support_type="unsupported",
        support_scope=(
            "bounded synthetic/local demonstration claim requiring a completed "
            "uv-local experiment artifact"
        ),
        unsupported_reason=(
            "no completed uv-local synthetic experiment artifact is linked to this "
            "bounded demonstration claim"
        ),
    )


def _proof_link(
    *,
    base: dict[str, Any],
    proof_artifacts: list[ProofArtifact],
    match_tokens: set[str],
    requires_support: bool,
) -> ClaimEvidenceMapLink:
    matching = [
        proof
        for proof in proof_artifacts
        if match_tokens.intersection(proof.claim_ids_or_statement_ids)
    ]
    formal = [proof for proof in matching if _proof_is_formal_passed(proof)]
    if formal:
        return _make_link(
            **base,
            requires_support=requires_support,
            support_status="supported_within_scope",
            classification="proof_supported_claim",
            supporting_proof_artifact_ids=[proof.proof_id for proof in formal],
            support_type="formal_proof_verification",
            support_scope=(
                "formal proof artifact with passed checker for declared claim ID "
                "or statement hash"
            ),
            unsupported_reason=None,
        )
    informal = [proof for proof in matching if proof.proof_type in _INFORMAL_PROOF_TYPES]
    if informal:
        return _make_link(
            **base,
            requires_support=requires_support,
            support_status="partially_supported",
            classification="proof_supported_claim",
            supporting_proof_artifact_ids=[proof.proof_id for proof in informal],
            support_type="informal_proof_context",
            support_scope="informal proof context only; not formal verification",
            unsupported_reason="no matching formal proof artifact with passed checker",
        )
    failed = [proof for proof in matching if proof.checker_status != "passed"]
    return _make_link(
        **base,
        requires_support=requires_support,
        support_status="unsupported",
        classification="unsupported_claim",
        supporting_proof_artifact_ids=[proof.proof_id for proof in failed],
        support_type="unsupported",
        support_scope="proof artifact did not provide accepted formal support",
        unsupported_reason=(
            "matching proof artifacts failed or were inconclusive"
            if failed
            else "no matching proof artifact"
        ),
    )


def _experiment_link(
    *,
    base: dict[str, Any],
    experiment_artifacts: list[ExperimentArtifact],
    match_tokens: set[str],
    requires_support: bool,
) -> ClaimEvidenceMapLink | None:
    if base["claim_class"] == "proof_claim":
        return _make_link(
            **base,
            requires_support=True,
            support_status="unsupported",
            classification="unsupported_claim",
            support_type="unsupported",
            support_scope="experiment artifacts cannot support proof claims",
            unsupported_reason="experiment artifact cannot support proof claim",
        )
    matching = [
        experiment
        for experiment in experiment_artifacts
        if match_tokens.intersection(experiment.claim_ids_or_section_ids)
    ]
    completed = [
        experiment
        for experiment in matching
        if experiment.status == "completed"
        and bool(experiment.metrics)
        and bool(experiment.result_summary.strip())
    ]
    if completed:
        return _make_link(
            **base,
            requires_support=requires_support,
            support_status="supported_within_scope",
            classification="experiment_supported_claim",
            supporting_experiment_artifact_ids=[
                experiment.experiment_id for experiment in completed
            ],
            support_type="experiment_result",
            support_scope="completed experiment artifact for declared claim ID or section only",
            unsupported_reason=None,
        )
    if matching and requires_support:
        return _make_link(
            **base,
            requires_support=True,
            support_status="unsupported",
            classification="unsupported_claim",
            supporting_experiment_artifact_ids=[
                experiment.experiment_id for experiment in matching
            ],
            support_type="unsupported",
            support_scope="experiment artifact was not completed or lacks bounded result details",
            unsupported_reason=(
                "matching experiment artifacts failed, were inconclusive, or lack "
                "metrics/results"
            ),
        )
    return None


def _human_review_link(
    *,
    base: dict[str, Any],
    item: ClaimSupportItem,
    human_review: HumanReviewArtifact | None,
) -> ClaimEvidenceMapLink | None:
    text = f"{item.sentence_snippet} {item.claim_class}".casefold()
    if human_review is None or (
        "human review" not in text and "reviewer" not in text
    ):
        return None
    if item.claim_class in {
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    }:
        return None
    return _make_link(
        **base,
        requires_support=False,
        support_status="supported_within_scope",
        classification="human_reviewed_claim",
        supporting_human_review_ids=[human_review.review_id],
        support_type="human_review_occurrence",
        support_scope="human review occurrence/status only",
        unsupported_reason=None,
    )


def _citation_link(
    *,
    base: dict[str, Any],
    item: ClaimSupportItem,
    citation_registry: CitationRegistry | None,
    retrieval_quality: RetrievalQualityReport | None,
) -> ClaimEvidenceMapLink:
    accepted = _accepted_citation_support(
        item=item,
        citation_registry=citation_registry,
        retrieval_quality=retrieval_quality,
    )
    if accepted:
        return _make_link(
            **base,
            requires_support=True,
            support_status="supported_within_scope",
            classification="citation_supported_background_claim",
            support_type="citation_background_context",
            support_scope="accepted registry citation for bounded background/source context only",
            unsupported_reason=None,
        )
    partial = bool(item.citation_keys_present or item.supporting_source_ids)
    return _make_link(
        **base,
        requires_support=True,
        support_status="partially_supported" if partial else "unsupported",
        classification="unsupported_claim",
        support_type="unsupported",
        support_scope="citation did not resolve to accepted registry source support",
        unsupported_reason=(
            item.unsupported_reason
            or "citation source is rejected, hard-rejected, or absent from registry"
        ),
    )


def _accepted_citation_support(
    *,
    item: ClaimSupportItem,
    citation_registry: CitationRegistry | None,
    retrieval_quality: RetrievalQualityReport | None,
) -> bool:
    if citation_registry is None or not item.citation_keys_present:
        return False
    by_key = {record.citation_key: record for record in citation_registry.citations}
    records = [by_key.get(key) for key in item.citation_keys_present]
    if not records or any(record is None for record in records):
        return False
    rejected = set(retrieval_quality.rejected_source_ids) if retrieval_quality else set()
    hard_rejected = {
        source_id
        for source_id, reason in (
            retrieval_quality.rejection_reasons.items() if retrieval_quality else []
        )
        if "hard" in reason.casefold()
    }
    accepted_source_ids = (
        set(retrieval_quality.accepted_source_ids) if retrieval_quality else set()
    )
    for record in records:
        assert record is not None
        if not record.accepted_for_registry or record.source_status == "rejected":
            return False
        if record.source_id in rejected or record.source_id in hard_rejected:
            return False
        if accepted_source_ids and record.source_id not in accepted_source_ids:
            return False
        if not record.may_support_background_context:
            return False
    return True


def _make_link(**kwargs: Any) -> ClaimEvidenceMapLink:
    kwargs.setdefault("supporting_proof_artifact_ids", [])
    kwargs.setdefault("supporting_experiment_artifact_ids", [])
    kwargs.setdefault("supporting_human_review_ids", [])
    return ClaimEvidenceMapLink(
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        **kwargs,
    )


def _summary_counts(links: list[ClaimEvidenceMapLink]) -> dict[str, int]:
    keys = {
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
        keys[link.support_status] += 1
        keys[link.classification] += 1
    keys["unsupported_non_scaffold_count"] = sum(
        1
        for link in links
        if link.support_status in {"unsupported", "blocked_forbidden_claim"}
        and link.requires_support
    )
    return keys


def _claim_class_for_label(claim: Claim) -> str:
    label = claim.claim_label.value
    if label == "LeanVerified":
        return "proof_claim"
    if label == "SyntheticExperimentVerified":
        return "experiment_claim"
    return "scaffold_statement" if not claim.allowed_in_main_text else "external_factual_claim"


def _proof_is_formal_passed(proof: ProofArtifact) -> bool:
    return proof.proof_type in _FORMAL_PROOF_TYPES and proof.checker_status == "passed"


def _standard_limitations() -> list[str]:
    return [
        (
            "Evidence support is scoped to declared artifact IDs, claim IDs, "
            "sections, or citation keys."
        ),
        (
            "This link does not imply novelty, broad correctness, scientific "
            "validation, or publication readiness."
        ),
    ]


def _read_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_proof_artifacts(run_path: Path) -> list[ProofArtifact]:
    reports = run_path / "reports"
    proofs: list[ProofArtifact] = []
    for path in sorted(reports.glob("proof-artifact-*.json")):
        if path.name.startswith("proof-artifact-index-") or path.name.endswith(
            ".meta.json"
        ):
            continue
        proof = _read_model(path, ProofArtifact)
        if proof is not None:
            proofs.append(proof)
    return sorted(proofs, key=lambda proof: proof.proof_id)


def _read_experiment_artifacts(run_path: Path) -> list[ExperimentArtifact]:
    reports = run_path / "reports"
    experiments: list[ExperimentArtifact] = []
    for path in sorted(reports.glob("experiment-artifact-*.json")):
        if path.name.startswith("experiment-artifact-index-") or path.name.endswith(
            ".meta.json"
        ):
            continue
        experiment = _read_model(path, ExperimentArtifact)
        if experiment is not None:
            experiments.append(experiment)
    return sorted(experiments, key=lambda experiment: experiment.experiment_id)


def _next_claim_evidence_map_id(root: Path, run_id: str) -> str:
    reports = root / "runs" / run_id / "reports"
    if not (reports / "claim-evidence-map.json").exists():
        return "claim-evidence-map"
    existing = [
        path
        for path in reports.glob("claim-evidence-map-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return f"claim-evidence-map-{len(existing) + 2:04d}"


def _next_claim_evidence_reviewer_summary_id(root: Path, run_id: str) -> str:
    existing = [
        path
        for path in (root / "runs" / run_id / "reports").glob(
            "reviewer-bundle-summary-after-claim-evidence-map-*.json"
        )
        if not path.name.endswith(".meta.json")
    ]
    return f"reviewer-bundle-summary-after-claim-evidence-map-{len(existing) + 1:04d}"


def _claim_evidence_map_sort_key(path: Path) -> tuple[int, str]:
    if path.name == "claim-evidence-map.json":
        return (0, path.name)
    match = re.match(r"claim-evidence-map-(\d+)\.json$", path.name)
    if match:
        return (int(match.group(1)), path.name)
    return (-1, path.name)


def _require_run_path(root: Path, run_id: str) -> Path:
    run_path = root / "runs" / run_id
    if not run_path.is_dir():
        raise ClaimEvidenceMapError(f"No run directory found for run_id={run_id}.")
    return run_path


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.casefold()))


__all__ = [
    "BOUNDED_EMPIRICAL_CLAIM_CLASSES",
    "BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID",
    "BOUNDED_EMPIRICAL_DEMONSTRATION_SECTION",
    "BOUNDED_EMPIRICAL_DEMONSTRATION_TEXT",
    "ClaimEvidenceMapError",
    "ClaimEvidenceMapPersistResult",
    "build_claim_evidence_map",
    "claim_evidence_summary_fields",
    "inspect_claim_evidence_map",
    "latest_claim_evidence_map_path",
    "latest_claim_support_audit_path",
    "persist_claim_evidence_map",
    "render_claim_evidence_map_markdown",
    "summary_count",
]
