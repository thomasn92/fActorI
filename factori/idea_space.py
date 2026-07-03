"""Read-only idea-space feature and diversity diagnostics over IdeaTree nodes."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
from factori.schemas import (
    ArtifactType,
    IdeaClusterDiagnostic,
    IdeaNode,
    IdeaNodeFeatureVector,
    IdeaSpaceAxis,
    IdeaSpaceDiversityReport,
    IdeaSpaceInspectionReport,
    IdeaSpacePCADiagnostic,
)

_IDEA_SPACE_EXPORT_RE = re.compile(r"^idea-space-report-(\d{4})\.(?:json|md)$")
_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")
_STOPWORDS = {
    "about",
    "after",
    "branch",
    "candidate",
    "claim",
    "current",
    "data",
    "evidence",
    "final",
    "form",
    "from",
    "geography",
    "heterogeneity",
    "human",
    "into",
    "method",
    "model",
    "paper",
    "regional",
    "scope",
    "spatial",
    "stage",
    "that",
    "the",
    "this",
    "with",
}

FEATURE_FLAGS = [
    "has_concrete_model",
    "has_equation",
    "has_algorithm",
    "has_DGP",
    "has_baseline",
    "has_metric",
    "has_ablation",
    "uses_synthetic_data",
    "uses_public_data",
    "uses_private_data",
    "uses_theorem_form",
    "uses_experiment_form",
    "uses_stress_testing",
    "uses_spatial_interaction_model",
    "uses_gravity_model",
    "uses_distance_decay",
    "uses_kernel_model",
    "uses_matrix_factorization",
    "uses_agent_based_model",
    "uses_robustness_model",
    "uses_uncertainty_model",
    "uses_PCA_or_representation_axis",
    "uses_dimensionality_reduction",
]

_MODEL_FLAGS = [
    "uses_spatial_interaction_model",
    "uses_gravity_model",
    "uses_distance_decay",
    "uses_kernel_model",
    "uses_matrix_factorization",
    "uses_agent_based_model",
    "uses_robustness_model",
    "uses_uncertainty_model",
    "uses_PCA_or_representation_axis",
    "uses_dimensionality_reduction",
]

_VALIDATION_MODE_TERMS = {
    "boundary",
    "bounded",
    "conjecture",
    "contract",
    "deferred",
    "experiment",
    "proof",
    "scope",
    "scoped",
    "synthetic",
    "theorem",
    "validation",
}

_PCA_BRANCH = {
    "model_idea": (
        "Represent regional OD-flow residuals after a pooled gravity baseline as "
        "a matrix R. Use PCA or low-rank factorization to identify dominant axes "
        "of spatial heterogeneity."
    ),
    "question": (
        "Do the leading components correspond to interpretable regional "
        "heterogeneity patterns beyond distance decay?"
    ),
    "synthetic_experiment": (
        "Generate OD flows with known latent regional factors and test whether "
        "PCA recovers them better than pooled distance-decay residuals."
    ),
}


class IdeaSpaceError(RuntimeError):
    """Raised when an idea-space diagnostic cannot be built or exported."""


def inspect_idea_space(
    *,
    run_id: str,
    root: str | Path = ".",
) -> IdeaSpaceInspectionReport:
    """Build a read-only PCA-like idea-space diagnostic for one run."""
    diversity = build_idea_space_diversity_report(run_id=run_id, root=root)
    return IdeaSpaceInspectionReport(
        run_id=diversity.run_id,
        tree_present=diversity.tree_present,
        node_count=diversity.node_count,
        feature_count=diversity.feature_count,
        effective_rank=diversity.effective_rank,
        pc1_explained_variance=diversity.pc1_explained_variance,
        pc2_explained_variance=diversity.pc2_explained_variance,
        top_pc1_features=diversity.top_pc1_features,
        top_pc2_features=diversity.top_pc2_features,
        near_duplicate_node_pairs=diversity.near_duplicate_node_pairs,
        collapsed_axis_warnings=diversity.collapsed_axis_warnings,
        missing_axis_warnings=diversity.missing_axis_warnings,
        underexplored_scientific_axes=diversity.underexplored_scientific_axes,
        diversity_score=diversity.diversity_score,
        creativity_blockers=diversity.creativity_blockers,
        recommended_mutation_axes=diversity.recommended_mutation_axes,
        pca_inspired_branch=diversity.pca_inspired_branch,
        feature_vectors=diversity.feature_vectors,
        pca_diagnostic=diversity.pca_diagnostic,
        cluster_diagnostic=diversity.cluster_diagnostic,
        warnings=diversity.warnings,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def build_idea_space_diversity_report(
    *,
    run_id: str,
    root: str | Path = ".",
) -> IdeaSpaceDiversityReport:
    """Compute deterministic feature, PCA, duplicate, and mutation-axis diagnostics."""
    try:
        tree_report = inspect_idea_tree(run_id=run_id, root=root)
    except IdeaTreeError as exc:
        raise IdeaSpaceError(str(exc)) from exc

    vectors = [_feature_vector_for_node(node) for node in tree_report.nodes]
    pca = _pca_diagnostic(vectors)
    cluster = _cluster_diagnostic(tree_report.nodes, vectors)
    missing_axis_warnings = cluster.missing_axis_warnings
    collapsed_axis_warnings = cluster.collapsed_axis_warnings
    near_duplicates = cluster.near_duplicate_node_pairs
    diversity_score = _diversity_score(
        effective_rank=pca.effective_rank,
        collapsed_axis_warnings=collapsed_axis_warnings,
        missing_axis_warnings=missing_axis_warnings,
        near_duplicate_count=len(near_duplicates),
    )
    blockers = _creativity_blockers(
        diversity_score=diversity_score,
        cluster=cluster,
    )
    recommendations = _recommended_mutation_axes(vectors, tree_report.nodes)
    warnings = _deduplicate(
        [
            *tree_report.warnings,
            *collapsed_axis_warnings,
            *missing_axis_warnings,
        ]
    )
    return IdeaSpaceDiversityReport(
        run_id=run_id,
        tree_present=tree_report.tree_present,
        node_count=tree_report.node_count,
        feature_count=len(FEATURE_FLAGS),
        effective_rank=pca.effective_rank,
        pc1_explained_variance=pca.pc1_explained_variance,
        pc2_explained_variance=pca.pc2_explained_variance,
        top_pc1_features=pca.top_pc1_features,
        top_pc2_features=pca.top_pc2_features,
        near_duplicate_node_pairs=near_duplicates,
        collapsed_axis_warnings=collapsed_axis_warnings,
        missing_axis_warnings=missing_axis_warnings,
        underexplored_scientific_axes=cluster.underexplored_scientific_axes,
        diversity_score=diversity_score,
        creativity_blockers=blockers,
        recommended_mutation_axes=recommendations,
        pca_inspired_branch=_PCA_BRANCH,
        pca_diagnostic=pca,
        cluster_diagnostic=cluster,
        feature_vectors=vectors,
        source_idea_tree_warnings=tree_report.warnings,
        warnings=warnings,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def export_idea_space_report(
    *,
    run_id: str,
    export_format: str,
    root: str | Path = ".",
) -> str:
    """Write one append-only context-only idea-space report and return its path."""
    if export_format not in {"markdown", "json"}:
        raise IdeaSpaceError("Idea-space export format must be markdown or json.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise IdeaSpaceError(f"Reports directory not found for run_id={run_id}.")
    inspection = inspect_idea_space(run_id=run_id, root=root_path)
    number = _next_export_number(reports)
    export_id = f"idea-space-report-{number:04d}"
    store = ArtifactStore(root_path)
    metadata = {
        "stage": "idea_space_diagnostic",
        "format": export_format,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    if export_format == "json":
        artifact = store.write_json(
            run_id=run_id,
            artifact_id=export_id,
            artifact_type=ArtifactType.REPORT,
            data=inspection,
            metadata=metadata,
        )
    else:
        artifact = store.write_markdown(
            run_id=run_id,
            artifact_id=export_id,
            artifact_type=ArtifactType.REPORT,
            markdown=render_idea_space_markdown(inspection),
            metadata=metadata,
        )
    return artifact.path


def render_idea_space_text(report: IdeaSpaceInspectionReport) -> str:
    """Render the compact human-readable idea-space diagnostic."""
    lines = [
        f"Idea-space diversity: {report.diversity_score}",
        f"Effective rank: {report.effective_rank}",
        f"PC1 explained variance: {report.pc1_explained_variance:.3f}",
        f"PC2 explained variance: {report.pc2_explained_variance:.3f}",
    ]
    collapsed_phrase = report.cluster_diagnostic.main_collapsed_phrase_optional
    if collapsed_phrase:
        lines.append(f"Main collapsed phrase: {collapsed_phrase}")
    lines.append("Missing axes:")
    lines.extend(f"- {warning}" for warning in report.missing_axis_warnings or ["none"])
    lines.append("Collapsed axis warnings:")
    lines.extend(
        f"- {warning}" for warning in report.collapsed_axis_warnings or ["none"]
    )
    lines.append("Recommended mutation axes:")
    lines.extend(f"- {axis}" for axis in report.recommended_mutation_axes or ["none"])
    lines.append("publication_ready=false")
    return "\n".join(lines)


def render_idea_space_markdown(report: IdeaSpaceInspectionReport) -> str:
    """Render a context-only Markdown idea-space export."""
    recommendations = "\n".join(
        f"- {axis}" for axis in report.recommended_mutation_axes
    ) or "- none"
    blockers = "\n".join(f"- {blocker}" for blocker in report.creativity_blockers) or "- none"
    near_duplicates = "\n".join(
        "- {left} / {right}: title_jaccard={score:.2f}".format(
            left=pair.get("left_node_id", ""),
            right=pair.get("right_node_id", ""),
            score=float(pair.get("title_jaccard", 0.0)),
        )
        for pair in report.near_duplicate_node_pairs
    ) or "- none"
    return (
        "# Idea-Space Diversity Diagnostic\n\n"
        f"Run: `{report.run_id}`\n\n"
        "```text\n"
        f"{render_idea_space_text(report)}\n"
        "```\n\n"
        "## PCA-Inspired Branch\n\n"
        f"- Model idea: {report.pca_inspired_branch.get('model_idea', '')}\n"
        f"- Question: {report.pca_inspired_branch.get('question', '')}\n"
        f"- Synthetic experiment: "
        f"{report.pca_inspired_branch.get('synthetic_experiment', '')}\n\n"
        "## Creativity Blockers\n\n"
        f"{blockers}\n\n"
        "## Near Duplicates\n\n"
        f"{near_duplicates}\n\n"
        "## Recommended Mutation Axes\n\n"
        f"{recommendations}\n\n"
        "## Evidence Boundary\n\n"
        "This report is provenance/context only. It creates no proof, experiment, "
        "citation, validation, or publication-readiness authority.\n\n"
        "publication_ready=false\n"
    )


def _feature_vector_for_node(node: IdeaNode) -> IdeaNodeFeatureVector:
    text = _node_text(node)
    scientific_text = _node_text(node, include_reasons=False)
    flags = {
        "uses_spatial_interaction_model": _contains(text, "spatial interaction"),
        "uses_gravity_model": _contains(text, "gravity"),
        "uses_distance_decay": _contains(text, "distance decay", "distance-decay"),
        "uses_PCA_or_representation_axis": _contains(
            text,
            "pca",
            "principal component",
            "representation axis",
            "low-rank",
        ),
        "uses_kernel_model": _contains(text, "kernel", "kernelized"),
        "uses_matrix_factorization": _contains(
            text,
            "matrix factorization",
            "matrix-factorized",
            "low-rank factorization",
        ),
        "uses_agent_based_model": _contains(text, "agent-based", "agent based"),
        "uses_stress_testing": _contains(text, "stress testing", "stress-test"),
        "uses_synthetic_data": _contains(text, "synthetic", "simulated", "simulation"),
        "uses_public_data": _contains(text, "public data", "open data"),
        "uses_private_data": _contains(text, "private user data", "private data"),
        "uses_theorem_form": _contains(text, "theorem", "conjecture"),
        "uses_experiment_form": _contains(
            text,
            "experiment",
            "empirical demonstration",
            "ablation",
            "calibration",
        ),
        "uses_robustness_model": _contains(text, "robustness", "robust"),
        "uses_uncertainty_model": _contains(text, "uncertainty", "sensitivity"),
        "uses_dimensionality_reduction": _contains(
            text,
            "dimensionality reduction",
            "low-rank",
            "pca",
            "principal component",
            "embedding",
        ),
    }
    flags["has_equation"] = bool(
        re.search(r"\b[a-z][a-z0-9_]{1,12}\s*=\s*[^,;]+", scientific_text)
        or re.search(
            r"\b(equation|formula|residual matrix|matrix r)\b",
            scientific_text,
        )
    )
    flags["has_algorithm"] = _contains(
        text,
        "algorithm",
        "estimator",
        "optimization",
        "procedure",
        "pipeline",
    )
    flags["has_DGP"] = _contains(
        text,
        "dgp",
        "data generating process",
        "data-generating process",
        "generative process",
    )
    flags["has_baseline"] = _contains(
        text,
        "baseline",
        "benchmark",
        "comparator",
        "null model",
        "pooled gravity",
    )
    flags["has_metric"] = _contains(
        text,
        "metric",
        "rmse",
        "mae",
        "error",
        "accuracy",
        "calibration",
        "score",
    )
    flags["has_ablation"] = _contains(text, "ablation", "ablate")
    flags["has_concrete_model"] = any(flags[name] for name in _MODEL_FLAGS)
    feature_values = {name: 1.0 if flags.get(name, False) else 0.0 for name in FEATURE_FLAGS}
    active = [name for name in FEATURE_FLAGS if feature_values[name] > 0.0]
    return IdeaNodeFeatureVector(
        node_id=node.node_id,
        title=node.title,
        stage_origin=node.stage_origin,
        status=node.status,
        domain_features=_group(feature_values, ["uses_spatial_interaction_model"]),
        model_features=_group(
            feature_values,
            [
                "has_concrete_model",
                "uses_spatial_interaction_model",
                "uses_gravity_model",
                "uses_distance_decay",
                "uses_kernel_model",
                "uses_matrix_factorization",
                "uses_agent_based_model",
                "uses_robustness_model",
                "uses_uncertainty_model",
                "uses_PCA_or_representation_axis",
                "uses_dimensionality_reduction",
            ],
        ),
        method_features=_group(feature_values, ["has_algorithm", "uses_stress_testing"]),
        data_regime_features=_group(
            feature_values,
            ["uses_synthetic_data", "uses_public_data", "uses_private_data"],
        ),
        baseline_features=_group(feature_values, ["has_baseline", "has_metric"]),
        experiment_features=_group(
            feature_values,
            [
                "uses_experiment_form",
                "has_DGP",
                "has_metric",
                "has_ablation",
                "uses_stress_testing",
            ],
        ),
        mathematical_object_features=_group(
            feature_values,
            ["has_equation", "uses_theorem_form", "uses_PCA_or_representation_axis"],
        ),
        claim_type_features=_group(
            feature_values,
            ["uses_theorem_form", "uses_experiment_form"],
        ),
        risk_features=_group(feature_values, ["uses_private_data"]),
        **{name: bool(flags.get(name, False)) for name in FEATURE_FLAGS},
        feature_values=feature_values,
        active_feature_names=active,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def _pca_diagnostic(vectors: list[IdeaNodeFeatureVector]) -> IdeaSpacePCADiagnostic:
    if not vectors:
        return IdeaSpacePCADiagnostic(
            method="empty_feature_matrix",
            effective_rank=0,
            pc1_explained_variance=0.0,
            pc2_explained_variance=0.0,
            axes=[],
            top_pc1_features=[],
            top_pc2_features=[],
        )
    matrix = [
        [vector.feature_values.get(feature, 0.0) for feature in FEATURE_FLAGS]
        for vector in vectors
    ]
    try:
        return _numpy_svd_diagnostic(matrix)
    except Exception:
        return _variance_fallback_diagnostic(matrix)


def _numpy_svd_diagnostic(matrix: list[list[float]]) -> IdeaSpacePCADiagnostic:
    import numpy as np  # type: ignore[import-not-found]

    array = np.asarray(matrix, dtype=float)
    centered = array - array.mean(axis=0, keepdims=True)
    if centered.size == 0:
        singular_values = np.asarray([])
        components = np.zeros((0, len(FEATURE_FLAGS)))
    else:
        _, singular_values, components = np.linalg.svd(centered, full_matrices=False)
    variances = singular_values**2
    total = float(variances.sum())
    explained = [float(value / total) if total else 0.0 for value in variances]
    effective_rank = int((singular_values > 1e-9).sum())
    axes = [
        _axis_from_loadings(
            axis_id=f"pc{index + 1}",
            explained_variance=explained[index] if index < len(explained) else 0.0,
            loadings=[
                float(value) for value in components[index]
            ] if index < len(components) else [0.0 for _ in FEATURE_FLAGS],
        )
        for index in range(min(2, len(FEATURE_FLAGS)))
    ]
    return IdeaSpacePCADiagnostic(
        method="centered_binary_feature_svd",
        effective_rank=effective_rank,
        pc1_explained_variance=axes[0].explained_variance if axes else 0.0,
        pc2_explained_variance=axes[1].explained_variance if len(axes) > 1 else 0.0,
        axes=axes,
        top_pc1_features=axes[0].top_positive_features[:5] if axes else [],
        top_pc2_features=axes[1].top_positive_features[:5] if len(axes) > 1 else [],
    )


def _variance_fallback_diagnostic(matrix: list[list[float]]) -> IdeaSpacePCADiagnostic:
    rows = len(matrix)
    columns = len(FEATURE_FLAGS)
    means = [
        sum(row[index] for row in matrix) / rows if rows else 0.0
        for index in range(columns)
    ]
    variances = [
        sum((row[index] - means[index]) ** 2 for row in matrix)
        for index in range(columns)
    ]
    total = sum(variances)
    ranked = sorted(range(columns), key=lambda index: variances[index], reverse=True)
    effective_rank = sum(variance > 1e-9 for variance in variances)
    axes = []
    for axis_index, feature_index in enumerate(ranked[:2]):
        loadings = [0.0 for _ in FEATURE_FLAGS]
        if total > 0.0 and variances[feature_index] > 0.0:
            loadings[feature_index] = 1.0
        axes.append(
            _axis_from_loadings(
                axis_id=f"pc{axis_index + 1}",
                explained_variance=(variances[feature_index] / total if total else 0.0),
                loadings=loadings,
            )
        )
    return IdeaSpacePCADiagnostic(
        method="centered_binary_feature_variance_fallback",
        effective_rank=effective_rank,
        pc1_explained_variance=axes[0].explained_variance if axes else 0.0,
        pc2_explained_variance=axes[1].explained_variance if len(axes) > 1 else 0.0,
        axes=axes,
        top_pc1_features=axes[0].top_positive_features[:5] if axes else [],
        top_pc2_features=axes[1].top_positive_features[:5] if len(axes) > 1 else [],
    )


def _axis_from_loadings(
    *,
    axis_id: str,
    explained_variance: float,
    loadings: list[float],
) -> IdeaSpaceAxis:
    loading_map = {
        feature: round(loadings[index], 6)
        for index, feature in enumerate(FEATURE_FLAGS)
        if abs(loadings[index]) > 1e-9
    }
    positives = sorted(
        ((feature, value) for feature, value in loading_map.items() if value > 0.0),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    negatives = sorted(
        ((feature, value) for feature, value in loading_map.items() if value < 0.0),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    top_positive = [feature for feature, _ in positives[:5]]
    top_negative = [feature for feature, _ in negatives[:5]]
    interpretation = (
        f"{axis_id} is dominated by {', '.join(top_positive[:3])}"
        if top_positive
        else f"{axis_id} has no dominant positive feature"
    )
    return IdeaSpaceAxis(
        axis_id=axis_id,
        explained_variance=round(max(0.0, min(1.0, explained_variance)), 6),
        feature_loadings=loading_map,
        top_positive_features=top_positive,
        top_negative_features=top_negative,
        interpretation=interpretation,
    )


def _cluster_diagnostic(
    nodes: list[IdeaNode],
    vectors: list[IdeaNodeFeatureVector],
) -> IdeaClusterDiagnostic:
    vector_by_id = {vector.node_id: vector for vector in vectors}
    near_duplicates = _near_duplicate_pairs(nodes)
    main_phrase = _main_collapsed_phrase(nodes)
    collapsed_warnings: list[str] = []
    if main_phrase is not None:
        collapsed_warnings.append(
            f"Too many nodes share repeated title phrase: {main_phrase}."
        )
    if near_duplicates:
        collapsed_warnings.append(
            f"{len(near_duplicates)} near-duplicate idea-title pairs were detected."
        )
    if _model_signature_collapse(vectors):
        collapsed_warnings.append("Too many nodes share the same method/model flags.")
    validation_mode_count = _validation_mode_only_variant_count(nodes, vector_by_id)
    if validation_mode_count:
        collapsed_warnings.append(
            "Stage B variants are validation-mode variants, not "
            "scientific-mechanism variants."
        )

    missing_warnings: list[str] = []
    if not any(vector.has_concrete_model for vector in vectors):
        missing_warnings.append("No node has a concrete model axis.")
    if not any(vector.has_equation for vector in vectors):
        missing_warnings.append("No node has an equation or model-object axis.")
    if not any(vector.has_DGP for vector in vectors):
        missing_warnings.append("No node has a DGP or data-generating-process axis.")
    if not any(vector.has_baseline and vector.has_metric for vector in vectors):
        missing_warnings.append("No baseline metric axis is represented.")
    data_regimes = {
        name
        for vector in vectors
        for name in ("uses_synthetic_data", "uses_public_data", "uses_private_data")
        if getattr(vector, name)
    }
    if len(data_regimes) <= 1:
        missing_warnings.append("No data-regime diversity is represented.")
    if not any(
        vector.uses_spatial_interaction_model
        or vector.uses_gravity_model
        or vector.uses_distance_decay
        for vector in vectors
    ):
        missing_warnings.append(
            "No spatial interaction, gravity, or distance-decay model axis is represented."
        )
    underexplored = _underexplored_axes(missing_warnings, vectors)
    return IdeaClusterDiagnostic(
        near_duplicate_node_pairs=near_duplicates,
        collapsed_axis_warnings=_deduplicate(collapsed_warnings),
        missing_axis_warnings=_deduplicate(missing_warnings),
        underexplored_scientific_axes=underexplored,
        validation_mode_only_variant_count=validation_mode_count,
        main_collapsed_phrase_optional=main_phrase,
    )


def _near_duplicate_pairs(nodes: list[IdeaNode]) -> list[dict[str, str | float]]:
    pairs: list[dict[str, str | float]] = []
    stage_nodes = [node for node in nodes if node.stage_origin in {"stage_a", "stage_b"}]
    tokens_by_id = {node.node_id: set(_tokens(node.title)) for node in stage_nodes}
    for left_index, left in enumerate(stage_nodes):
        for right in stage_nodes[left_index + 1 :]:
            left_tokens = tokens_by_id[left.node_id]
            right_tokens = tokens_by_id[right.node_id]
            if not left_tokens or not right_tokens:
                continue
            score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
            if score >= 0.6:
                pairs.append(
                    {
                        "left_node_id": left.node_id,
                        "right_node_id": right.node_id,
                        "title_jaccard": round(score, 6),
                    }
                )
    return pairs


def _main_collapsed_phrase(nodes: list[IdeaNode]) -> str | None:
    counter: Counter[str] = Counter()
    for node in nodes:
        if node.stage_origin not in {"stage_a", "stage_b"}:
            continue
        tokens = _tokens(node.title)
        for size in (3, 2):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase = " ".join(tokens[index : index + size])
                if phrase:
                    counter[phrase] += 1
    repeated = [
        (phrase, count)
        for phrase, count in counter.items()
        if count >= 2 and any(token not in _STOPWORDS for token in phrase.split())
    ]
    if not repeated:
        return None
    repeated.sort(key=lambda item: (item[1], len(item[0])), reverse=True)
    return repeated[0][0]


def _model_signature_collapse(vectors: list[IdeaNodeFeatureVector]) -> bool:
    if len(vectors) < 4:
        return False
    signatures = Counter(
        tuple(name for name in _MODEL_FLAGS if vector.feature_values.get(name, 0.0))
        for vector in vectors
        if vector.stage_origin in {"stage_a", "stage_b"}
    )
    if not signatures:
        return False
    return signatures.most_common(1)[0][1] / len(vectors) >= 0.6


def _validation_mode_only_variant_count(
    nodes: list[IdeaNode],
    vector_by_id: dict[str, IdeaNodeFeatureVector],
) -> int:
    count = 0
    for node in nodes:
        if node.stage_origin != "stage_b":
            continue
        tokens = set(_tokens(_node_text(node)))
        vector = vector_by_id.get(node.node_id)
        if vector is None:
            continue
        if tokens & _VALIDATION_MODE_TERMS and not vector.has_concrete_model:
            count += 1
    return count


def _underexplored_axes(
    missing_warnings: list[str],
    vectors: list[IdeaNodeFeatureVector],
) -> list[str]:
    axes: list[str] = []
    if any("concrete model" in warning for warning in missing_warnings):
        axes.append("concrete model")
    if any("equation" in warning for warning in missing_warnings):
        axes.append("equation or model object")
    if any("DGP" in warning for warning in missing_warnings):
        axes.append("DGP")
    if any("spatial interaction" in warning for warning in missing_warnings):
        axes.append("spatial interaction / gravity / distance decay")
    if any("baseline metric" in warning for warning in missing_warnings):
        axes.append("baseline metric")
    if any("data-regime" in warning for warning in missing_warnings):
        axes.append("data-regime diversity")
    if not any(vector.uses_kernel_model for vector in vectors):
        axes.append("kernel spatial interaction")
    if not any(vector.uses_matrix_factorization for vector in vectors):
        axes.append("matrix factorization")
    if not any(vector.uses_agent_based_model for vector in vectors):
        axes.append("agent-based mechanism")
    if not any(vector.uses_PCA_or_representation_axis for vector in vectors):
        axes.append("PCA / low-rank representation")
    return _deduplicate(axes)


def _diversity_score(
    *,
    effective_rank: int,
    collapsed_axis_warnings: list[str],
    missing_axis_warnings: list[str],
    near_duplicate_count: int,
) -> str:
    if (
        effective_rank < 3
        or near_duplicate_count > 0
        or len(collapsed_axis_warnings) >= 2
        or len(missing_axis_warnings) >= 3
    ):
        return "low"
    if effective_rank < 6 or collapsed_axis_warnings or missing_axis_warnings:
        return "moderate"
    return "high"


def _creativity_blockers(
    *,
    diversity_score: str,
    cluster: IdeaClusterDiagnostic,
) -> list[str]:
    blockers = []
    if diversity_score == "low":
        blockers.append("idea tree has low effective feature rank")
    if cluster.main_collapsed_phrase_optional:
        blockers.append(
            f"repeated phrase: {cluster.main_collapsed_phrase_optional}"
        )
    blockers.extend(cluster.missing_axis_warnings)
    blockers.extend(cluster.collapsed_axis_warnings)
    return _deduplicate(blockers)


def _recommended_mutation_axes(
    vectors: list[IdeaNodeFeatureVector],
    nodes: list[IdeaNode],
) -> list[str]:
    text = " ".join(_node_text(node) for node in nodes).lower()
    human_geography = "human geography" in text or "spatial" in text
    recommendations: list[str] = []
    if human_geography or not any(vector.uses_gravity_model for vector in vectors):
        recommendations.append("region-specific distance-decay gravity model")
    if human_geography or not any(
        vector.uses_PCA_or_representation_axis for vector in vectors
    ):
        recommendations.append("PCA/low-rank OD-flow representation model")
    if human_geography or not any(vector.uses_kernel_model for vector in vectors):
        recommendations.append("kernelized spatial interaction model")
    if human_geography or not any(
        vector.uses_matrix_factorization for vector in vectors
    ):
        recommendations.append("matrix-factorized regional mobility model")
    if human_geography or not any(vector.uses_agent_based_model for vector in vectors):
        recommendations.append("agent-based synthetic mobility model")
    if not any(vector.has_DGP for vector in vectors):
        recommendations.append("explicit synthetic DGP with known latent heterogeneity")
    if not any(vector.has_baseline and vector.has_metric for vector in vectors):
        recommendations.append("baseline metric axis for pooled distance-decay residuals")
    return _deduplicate(recommendations)


def _node_text(node: IdeaNode, *, include_reasons: bool = True) -> str:
    values = [
        node.title,
        node.domain,
        node.method_optional,
        node.research_question_optional,
        node.hypothesis_optional,
        node.model_hint_optional,
        node.experiment_hint_optional,
        node.baseline_hint_optional,
        node.data_regime_optional,
    ]
    if include_reasons:
        values.extend([node.prune_reason_optional, node.survivor_reason_optional])
    return " ".join(value for value in values if value).lower()


def _contains(text: str, *phrases: str) -> bool:
    return any(phrase.lower() in text for phrase in phrases)


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in _WORD_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _group(feature_values: dict[str, float], names: list[str]) -> dict[str, float]:
    return {name: feature_values[name] for name in names}


def _next_export_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.iterdir()
        if path.is_file() and (match := _IDEA_SPACE_EXPORT_RE.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "FEATURE_FLAGS",
    "IdeaSpaceError",
    "build_idea_space_diversity_report",
    "export_idea_space_report",
    "inspect_idea_space",
    "render_idea_space_markdown",
    "render_idea_space_text",
]
