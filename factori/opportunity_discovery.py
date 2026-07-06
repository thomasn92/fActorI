"""General deterministic Stage 0 opportunity discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.commands import ensure_run_initialized
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ControllerActionType,
    DomainPrimitive,
    MethodLens,
    OpportunityCandidate,
    OpportunityDiscoveryInspectionReport,
    OpportunityDiscoveryReport,
    OpportunityScoreBreakdown,
    OpportunitySeedConstraint,
    ScientificStageKind,
)

OPPORTUNITY_THRESHOLD = 0.70
_DISCOVERY_RE = re.compile(r"^opportunity-discovery-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class OpportunityDiscoveryError(RuntimeError):
    """Raised when Stage 0 opportunity discovery cannot proceed."""


@dataclass(frozen=True)
class OpportunityDiscoveryResult:
    """Persisted Stage 0 opportunity discovery result."""

    run_id: str
    report: OpportunityDiscoveryReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


_DOMAIN_PRIMITIVES: dict[str, list[tuple[str, str, str, list[str], list[str]]]] = {
    "human geography": [
        (
            "flows",
            "relational_process",
            "Movements between places or regions.",
            ["flow", "od", "network", "transport"],
            ["origin-destination commuting flows"],
        ),
        (
            "distance",
            "spatial_measure",
            "Spatial separation or travel impedance.",
            ["distance", "metric", "geometry", "decay"],
            ["inter-region distance matrix"],
        ),
        (
            "migration",
            "mobility_process",
            "Longer-run relocation between regions.",
            ["flow", "mobility", "temporal"],
            ["regional migration counts"],
        ),
        (
            "commuting",
            "mobility_process",
            "Repeated work-travel movement.",
            ["flow", "mobility", "od", "network"],
            ["home-work OD table"],
        ),
        (
            "accessibility",
            "spatial_measure",
            "Access to jobs, services, or amenities.",
            ["access", "distance", "optimization"],
            ["job accessibility index"],
        ),
        (
            "spatial inequality",
            "distributional_pattern",
            "Uneven regional exposure or opportunity.",
            ["inequality", "risk", "distribution"],
            ["regional deprivation gradient"],
        ),
        (
            "regional dependence",
            "spatial_dependence",
            "Dependence between nearby or linked regions.",
            ["dependence", "network", "spatial", "graph"],
            ["neighbor dependence matrix"],
        ),
        (
            "urban networks",
            "network_object",
            "City or region systems with links.",
            ["network", "graph", "flow"],
            ["urban commuting graph"],
        ),
        (
            "mobility",
            "mobility_process",
            "Movement or activity between places.",
            ["mobility", "flow", "trajectory"],
            ["daily mobility trace aggregate"],
        ),
        (
            "segregation",
            "distributional_pattern",
            "Group separation across space or networks.",
            ["inequality", "partition", "topology"],
            ["spatial segregation index"],
        ),
        (
            "boundary effects",
            "spatial_partition",
            "Sensitivity to region definitions and aggregation.",
            ["boundary", "partition", "robustness", "scale"],
            ["administrative boundary perturbation"],
        ),
    ],
    "market microstructure": [
        (
            "order flow",
            "market_process",
            "Signed market order arrivals.",
            ["flow", "stochastic", "self_excitation"],
            ["signed order-flow sequence"],
        ),
        (
            "limit order book",
            "market_state",
            "Queued liquidity across prices.",
            ["queue", "graph", "state"],
            ["top-of-book depth ladder"],
        ),
        (
            "arrival intensity",
            "stochastic_intensity",
            "Rate of event arrivals.",
            ["stochastic", "intensity", "point_process"],
            ["limit order arrival rate"],
        ),
        (
            "spread",
            "price_measure",
            "Bid-ask price gap.",
            ["spread", "liquidity", "metric"],
            ["inside spread"],
        ),
        (
            "queue position",
            "market_state",
            "Priority in a limit-order queue.",
            ["queue", "rank", "state"],
            ["order queue rank"],
        ),
        (
            "self-excitation",
            "stochastic_dependence",
            "Events increasing near-future intensity.",
            ["hawkes", "stochastic", "dependence"],
            ["clustered trade arrivals"],
        ),
        (
            "price impact",
            "market_response",
            "Price response to signed flow.",
            ["impact", "causal", "flow"],
            ["impact curve"],
        ),
        (
            "latency",
            "market_constraint",
            "Time delay in observation or execution.",
            ["time", "latency", "control"],
            ["latency bucket"],
        ),
        (
            "liquidity",
            "market_capacity",
            "Capacity to trade with limited price movement.",
            ["liquidity", "depth", "risk"],
            ["available depth"],
        ),
        (
            "volatility",
            "risk_measure",
            "Short-run price variation.",
            ["volatility", "stochastic", "risk"],
            ["realized volatility"],
        ),
    ],
    "option surfaces": [
        (
            "strike",
            "contract_coordinate",
            "Option strike coordinate.",
            ["surface", "coordinate", "payoff"],
            ["moneyness bucket"],
        ),
        (
            "maturity",
            "contract_coordinate",
            "Option time-to-expiry coordinate.",
            ["surface", "time", "payoff"],
            ["expiry tenor"],
        ),
        (
            "implied volatility",
            "surface_value",
            "Market-implied volatility at strike and tenor.",
            ["volatility", "surface", "calibration"],
            ["IV grid"],
        ),
        (
            "smile",
            "surface_shape",
            "Strike curvature of implied volatility.",
            ["curvature", "surface", "geometry"],
            ["volatility smile"],
        ),
        (
            "arbitrage constraints",
            "financial_constraint",
            "No-arbitrage shape restrictions.",
            ["constraint", "duality", "convexity"],
            ["butterfly/calendar constraints"],
        ),
        (
            "calibration error",
            "model_gap",
            "Mismatch between model and observed surface.",
            ["calibration", "residual", "metric"],
            ["surface residual"],
        ),
    ],
    "insurance risk": [
        (
            "claim frequency",
            "risk_process",
            "Number of claims in a period.",
            ["count", "stochastic", "frequency"],
            ["annual claim count"],
        ),
        (
            "claim severity",
            "risk_distribution",
            "Loss size conditional on a claim.",
            ["loss", "tail", "distribution"],
            ["claim size"],
        ),
        (
            "tail dependence",
            "dependence_structure",
            "Joint extremes across risks.",
            ["tail", "copula", "dependence"],
            ["catastrophe loss dependence"],
        ),
        (
            "deductible",
            "contract_feature",
            "Loss retained before coverage.",
            ["payoff", "threshold", "contract"],
            ["deductible level"],
        ),
        (
            "capital requirement",
            "risk_measure",
            "Required buffer against losses.",
            ["risk", "capital", "constraint"],
            ["solvency capital"],
        ),
        (
            "reserve uncertainty",
            "risk_measure",
            "Uncertainty in future claim liabilities.",
            ["uncertainty", "time", "risk"],
            ["loss reserve distribution"],
        ),
    ],
    "robust finance": [
        (
            "loss distributions",
            "risk_distribution",
            "Distribution of portfolio losses.",
            ["loss", "distribution", "risk"],
            ["portfolio loss sample"],
        ),
        (
            "risk measures",
            "risk_functional",
            "Scalar summaries of downside exposure.",
            ["risk", "functional", "tail"],
            ["CVaR"],
        ),
        (
            "ambiguity sets",
            "uncertainty_set",
            "Sets of plausible probability laws.",
            ["ambiguity", "robust", "optimization"],
            ["Wasserstein ball"],
        ),
        (
            "Wasserstein distance",
            "probability_geometry",
            "Transport distance between distributions.",
            ["optimal_transport", "wasserstein", "geometry"],
            ["empirical distribution ball"],
        ),
        (
            "duality",
            "mathematical_tool",
            "Primal-dual reformulation.",
            ["duality", "convex", "optimization"],
            ["dual risk bound"],
        ),
        (
            "tail risk",
            "risk_pattern",
            "Extreme downside losses.",
            ["tail", "risk", "distribution"],
            ["loss quantile"],
        ),
        (
            "option payoff",
            "financial_payoff",
            "Contract payoff as a function of state.",
            ["payoff", "convexity", "option"],
            ["put payoff"],
        ),
        (
            "calibration error",
            "model_gap",
            "Mismatch between model and market inputs.",
            ["calibration", "residual", "risk"],
            ["surface calibration residual"],
        ),
    ],
}

_DOMAIN_ALIASES = {
    "spatial heterogeneity": "human geography",
    "geography": "human geography",
    "mobility": "human geography",
    "microstructure": "market microstructure",
    "limit order": "market microstructure",
    "option": "option surfaces",
    "volatility surface": "option surfaces",
    "insurance": "insurance risk",
    "actuarial": "insurance risk",
    "robust finance": "robust finance",
    "risk measure": "robust finance",
}

_METHOD_DATA: list[dict[str, Any]] = [
    {
        "id": "optimal_transport",
        "name": "optimal transport",
        "family": "probability geometry",
        "description": (
            "Moves mass between distributions or locations with explicit transport cost."
        ),
        "objects": ["couplings", "Wasserstein distances", "transport maps"],
        "claims": ["bounded representation claim", "synthetic comparison claim"],
        "baselines": ["Euclidean distance baseline", "pooled matching baseline"],
        "verify": ["synthetic experiment", "duality check"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["human geography", "robust finance", "option surfaces"],
        "tags": {
            "flow",
            "distance",
            "metric",
            "geometry",
            "optimal_transport",
            "distribution",
            "wasserstein",
            "mobility",
        },
        "underuse": 0.76,
    },
    {
        "id": "copulas",
        "name": "copulas",
        "family": "dependence modeling",
        "description": "Separates marginal behavior from dependence structure.",
        "objects": ["copula functions", "rank transforms", "tail-dependence coefficients"],
        "claims": ["dependence-structure claim", "tail comparison claim"],
        "baselines": ["independence copula", "Gaussian copula"],
        "verify": ["synthetic experiment", "tail-dependence diagnostic"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["insurance risk", "robust finance", "market microstructure"],
        "tags": {"dependence", "tail", "distribution", "risk", "copula", "rank"},
        "underuse": 0.68,
    },
    {
        "id": "graph_curvature",
        "name": "graph curvature",
        "family": "network geometry",
        "description": "Uses discrete curvature to characterize graph bottlenecks and robustness.",
        "objects": ["Ollivier curvature", "Forman curvature", "weighted graphs"],
        "claims": ["network-structure claim", "robustness diagnostic claim"],
        "baselines": ["degree centrality", "shortest-path distance"],
        "verify": ["synthetic graph experiment"],
        "data": ["synthetic", "public network"],
        "domains": ["human geography", "market microstructure"],
        "tags": {"network", "graph", "geometry", "curvature", "boundary", "dependence"},
        "underuse": 0.78,
    },
    {
        "id": "spatial_statistics",
        "name": "spatial statistics",
        "family": "spatial dependence",
        "description": "Models spatial autocorrelation, regional effects, and residual dependence.",
        "objects": ["spatial weights", "variograms", "Moran statistics"],
        "claims": ["spatial-dependence claim", "residual diagnostic claim"],
        "baselines": ["independent residual baseline", "pooled regional baseline"],
        "verify": ["synthetic spatial experiment"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["human geography", "insurance risk"],
        "tags": {"spatial", "dependence", "distance", "regional", "residual", "boundary"},
        "underuse": 0.56,
    },
    {
        "id": "stochastic_processes",
        "name": "stochastic processes",
        "family": "time and event dynamics",
        "description": "Represents time-indexed or event-indexed random processes.",
        "objects": ["point processes", "Markov chains", "diffusions"],
        "claims": ["dynamic mechanism claim", "arrival-intensity claim"],
        "baselines": ["Poisson process", "random walk"],
        "verify": ["simulation experiment", "moment check"],
        "data": ["synthetic", "public time series"],
        "domains": ["market microstructure", "insurance risk"],
        "tags": {"stochastic", "time", "intensity", "arrival", "self_excitation", "volatility"},
        "underuse": 0.50,
    },
    {
        "id": "topological_data_analysis",
        "name": "topological data analysis",
        "family": "shape diagnostics",
        "description": "Summarizes connected components, holes, and multi-scale shape.",
        "objects": ["persistence diagrams", "filtrations", "simplicial complexes"],
        "claims": ["shape-diagnostic claim", "multi-scale structure claim"],
        "baselines": ["cluster count baseline"],
        "verify": ["synthetic topology experiment"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["human geography", "option surfaces"],
        "tags": {"topology", "shape", "partition", "scale", "network", "surface"},
        "underuse": 0.72,
    },
    {
        "id": "causal_inference",
        "name": "causal inference",
        "family": "identification and interventions",
        "description": (
            "Frames assumptions under which interventions or contrasts can be interpreted."
        ),
        "objects": ["DAGs", "potential outcomes", "identification assumptions"],
        "claims": ["identification-boundary claim", "negative-control claim"],
        "baselines": ["associational baseline"],
        "verify": ["synthetic causal DGP"],
        "data": ["synthetic", "public observational"],
        "domains": ["human geography", "market microstructure", "insurance risk"],
        "tags": {"causal", "intervention", "baseline", "control", "effect", "impact"},
        "underuse": 0.58,
    },
    {
        "id": "information_geometry",
        "name": "information geometry",
        "family": "statistical manifold geometry",
        "description": "Uses geometric structure of statistical model families.",
        "objects": ["Fisher metrics", "statistical manifolds", "geodesics"],
        "claims": ["model-distance claim", "calibration-geometry claim"],
        "baselines": ["Euclidean parameter distance"],
        "verify": ["synthetic parameter experiment"],
        "data": ["synthetic"],
        "domains": ["option surfaces", "robust finance"],
        "tags": {"geometry", "distribution", "calibration", "metric", "parameter"},
        "underuse": 0.74,
    },
    {
        "id": "robust_optimization",
        "name": "robust optimization",
        "family": "optimization under uncertainty",
        "description": "Optimizes against explicit uncertainty sets.",
        "objects": ["uncertainty sets", "robust counterparts", "minimax objectives"],
        "claims": ["robust-bound claim", "stress-test claim"],
        "baselines": ["nominal optimizer"],
        "verify": ["synthetic stress experiment", "duality check"],
        "data": ["synthetic"],
        "domains": ["robust finance", "insurance risk", "market microstructure"],
        "tags": {"robust", "optimization", "uncertainty", "risk", "stress", "constraint"},
        "underuse": 0.45,
    },
    {
        "id": "distributionally_robust_optimization",
        "name": "distributionally robust optimization",
        "family": "optimization under distributional ambiguity",
        "description": "Optimizes worst-case objectives over an ambiguity set of distributions.",
        "objects": ["ambiguity sets", "Wasserstein balls", "dual reformulations"],
        "claims": ["distributional-robustness claim", "bounded worst-case claim"],
        "baselines": ["empirical risk optimizer", "nominal optimizer"],
        "verify": ["synthetic ambiguity experiment", "duality check"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["robust finance", "insurance risk"],
        "tags": {
            "ambiguity",
            "robust",
            "optimization",
            "wasserstein",
            "distribution",
            "risk",
            "duality",
        },
        "underuse": 0.52,
    },
    {
        "id": "kernel_methods",
        "name": "kernel methods",
        "family": "nonlinear representation",
        "description": "Uses positive-definite kernels to encode nonlinear similarity.",
        "objects": ["kernels", "RKHS functions", "Gram matrices"],
        "claims": ["nonlinear-representation claim", "bounded predictive comparison claim"],
        "baselines": ["linear model", "distance-decay baseline"],
        "verify": ["synthetic regression experiment"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["human geography", "option surfaces", "market microstructure"],
        "tags": {"kernel", "similarity", "nonlinear", "distance", "surface", "metric", "regional"},
        "underuse": 0.70,
    },
    {
        "id": "matrix_factorization",
        "name": "matrix factorization",
        "family": "low-rank representation",
        "description": "Represents matrices through latent low-dimensional factors.",
        "objects": ["low-rank factors", "singular vectors", "matrix residuals"],
        "claims": ["latent-factor claim", "reconstruction claim"],
        "baselines": ["pooled mean baseline", "unfactorized residual baseline"],
        "verify": ["synthetic low-rank experiment"],
        "data": ["synthetic", "public aggregate"],
        "domains": ["human geography", "market microstructure", "option surfaces"],
        "tags": {"matrix", "low_rank", "flow", "od", "residual", "surface", "factor", "mobility"},
        "underuse": 0.80,
    },
    {
        "id": "agent_based_modeling",
        "name": "agent-based modeling",
        "family": "simulation mechanisms",
        "description": "Simulates micro-level agents to generate aggregate patterns.",
        "objects": ["agents", "rules", "emergent aggregates"],
        "claims": ["mechanism-demonstration claim", "synthetic emergence claim"],
        "baselines": ["aggregate random-choice baseline"],
        "verify": ["synthetic simulation experiment"],
        "data": ["synthetic"],
        "domains": ["human geography", "market microstructure", "insurance risk"],
        "tags": {"agent", "mobility", "network", "simulation", "behavior", "arrival"},
        "underuse": 0.62,
    },
    {
        "id": "convex_duality",
        "name": "convex duality",
        "family": "optimization theory",
        "description": "Derives dual problems, certificates, and bounds for convex programs.",
        "objects": ["dual variables", "support functions", "convex conjugates"],
        "claims": ["bounded theorem claim", "dual-certificate claim"],
        "baselines": ["primal-only formulation"],
        "verify": ["formal proof plan", "deterministic numeric check"],
        "data": ["synthetic", "no data"],
        "domains": ["robust finance", "option surfaces"],
        "tags": {"duality", "convex", "optimization", "constraint", "risk", "payoff"},
        "underuse": 0.60,
    },
    {
        "id": "pde_diffusion",
        "name": "PDE / diffusion",
        "family": "continuous dynamics",
        "description": (
            "Represents evolution by local differential operators or diffusion equations."
        ),
        "objects": ["diffusion PDEs", "operators", "boundary conditions"],
        "claims": ["continuous-limit claim", "diffusion-dynamics claim"],
        "baselines": ["random-walk baseline"],
        "verify": ["synthetic PDE simulation", "boundary-condition check"],
        "data": ["synthetic"],
        "domains": ["option surfaces", "insurance risk"],
        "tags": {"diffusion", "pde", "time", "boundary", "surface", "migration"},
        "underuse": 0.64,
    },
    {
        "id": "network_science",
        "name": "network science",
        "family": "graph structure",
        "description": "Studies nodes, links, communities, centrality, and network flows.",
        "objects": ["weighted graphs", "communities", "centrality scores"],
        "claims": ["network-structure claim", "community or flow claim"],
        "baselines": ["degree baseline", "configuration model"],
        "verify": ["synthetic network experiment"],
        "data": ["synthetic", "public network"],
        "domains": ["human geography", "market microstructure"],
        "tags": {"network", "graph", "flow", "community", "dependence", "queue", "urban"},
        "underuse": 0.48,
    },
]


def method_lens_library(*, max_methods: int | None = None) -> list[MethodLens]:
    """Return the deterministic local Stage 0 method-lens library."""
    data = _METHOD_DATA if max_methods is None else _METHOD_DATA[:max_methods]
    return [_method_lens(item) for item in data]


def extract_domain_primitives(domain: str) -> list[DomainPrimitive]:
    """Extract deterministic primitives for a domain-only query."""
    domain_name = _domain_family(domain)
    raw = _DOMAIN_PRIMITIVES.get(domain_name)
    if raw is None:
        raw = _fallback_primitives(domain)
    return [
        DomainPrimitive(
            primitive_id=f"primitive-{_slug(domain)}-{index:02d}-{_slug(name)}",
            domain=domain,
            name=name,
            primitive_type=primitive_type,
            description=description,
            symbolic_tags=tags,
            example_instantiations=examples,
        )
        for index, (name, primitive_type, description, tags, examples) in enumerate(raw, start=1)
    ]


def build_opportunity_discovery_report(
    *,
    run_id: str,
    domain: str,
    discovery_id: str = "opportunity-discovery-preview",
    max_methods: int = 20,
) -> OpportunityDiscoveryReport:
    """Build a deterministic Stage 0 opportunity report without persistence."""
    if not domain.strip():
        raise OpportunityDiscoveryError("domain is required.")
    if max_methods < 1:
        raise OpportunityDiscoveryError("max_methods must be at least 1.")
    primitives = extract_domain_primitives(domain)
    methods = method_lens_library(max_methods=max_methods)
    opportunities = [
        _opportunity_for_method(
            run_id=run_id,
            domain=domain,
            method=method,
            primitives=primitives,
        )
        for method in methods
    ]
    opportunities.sort(
        key=lambda candidate: (
            -candidate.score_breakdown.O_final,
            candidate.method_lens.name,
        )
    )
    seeds = [
        _seed_constraint(run_id=run_id, opportunity=opportunity, index=index)
        for index, opportunity in enumerate(
            [opportunity for opportunity in opportunities if opportunity.score_breakdown.promoted],
            start=1,
        )
    ]
    warnings = _report_warnings(opportunities)
    return OpportunityDiscoveryReport(
        run_id=run_id,
        discovery_id=discovery_id,
        domain=domain,
        primitive_count=len(primitives),
        method_lens_count=len(methods),
        opportunity_count=len(opportunities),
        promoted_count=len(seeds),
        promoted_method_ids=[seed.method_id for seed in seeds],
        seed_constraint_count=len(seeds),
        primitives=primitives,
        method_lenses=methods,
        opportunities=opportunities,
        seed_constraints=seeds,
        backend_records=[
            stage_backend_record(
                stage_id=discovery_id,
                stage_kind=ScientificStageKind.OPPORTUNITY_DISCOVERY,
                backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
                backend_name="deterministic_method_lens_library",
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason=(
                    "Domain primitives, method opportunities, questions, and seed constraints "
                    "come from deterministic local templates and heuristics."
                ),
                artifact_ids=[discovery_id],
            )
        ],
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def discover_opportunities(
    *,
    run_id: str,
    domain: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_methods: int = 20,
) -> OpportunityDiscoveryResult:
    """Persist deterministic Stage 0 opportunity discovery artifacts."""
    root_path = Path(root)
    ensure_run_initialized(root=root_path, run_id=run_id, store=store, ledger=ledger)
    reports = root_path / "runs" / run_id / "reports"
    number = _next_number(reports)
    discovery_id = f"opportunity-discovery-{number:04d}"
    report = build_opportunity_discovery_report(
        run_id=run_id,
        domain=domain,
        discovery_id=discovery_id,
        max_methods=max_methods,
    )
    metadata = {
        "stage": "stage0_opportunity_discovery",
        "artifact_role": "opportunity_discovery_context",
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
            ArtifactWriteSpec(
                discovery_id,
                ArtifactType.REPORT,
                report,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                f"{discovery_id}-markdown",
                ArtifactType.REPORT,
                render_opportunity_discovery_markdown(report),
                "markdown",
                metadata,
                filename_stem=discovery_id,
            ),
        ],
        action_type=ControllerActionType.OPPORTUNITY_DISCOVERY_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "domain": domain,
            "discovery_id": discovery_id,
            "primitive_count": report.primitive_count,
            "method_lens_count": report.method_lens_count,
            "opportunity_count": report.opportunity_count,
            "promoted_count": report.promoted_count,
            "seed_constraint_count": report.seed_constraint_count,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return OpportunityDiscoveryResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[discovery_id],
        markdown_artifact=by_id[f"{discovery_id}-markdown"],
    )


def inspect_opportunities(
    *,
    run_id: str,
    root: str | Path = ".",
) -> OpportunityDiscoveryInspectionReport:
    """Inspect the latest Stage 0 opportunity discovery report."""
    path = _latest_report_path(Path(root), run_id)
    if path is None:
        return OpportunityDiscoveryInspectionReport(
            run_id=run_id,
            opportunity_discovery_present=False,
            warnings=["No opportunity discovery report is present."],
            publication_ready=False,
        )
    try:
        report = OpportunityDiscoveryReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise OpportunityDiscoveryError(f"Could not load opportunity report: {exc}") from exc
    promoted = [
        opportunity for opportunity in report.opportunities if opportunity.score_breakdown.promoted
    ]
    rejected = [
        opportunity
        for opportunity in report.opportunities
        if not opportunity.score_breakdown.promoted
    ]
    return OpportunityDiscoveryInspectionReport(
        run_id=run_id,
        opportunity_discovery_present=True,
        latest_discovery_id_optional=report.discovery_id,
        domain_optional=report.domain,
        primitive_count=report.primitive_count,
        method_lens_count=report.method_lens_count,
        opportunity_count=report.opportunity_count,
        promoted_count=report.promoted_count,
        seed_constraint_count=report.seed_constraint_count,
        promoted_opportunities=promoted,
        rejected_opportunities=rejected,
        seed_constraints=report.seed_constraints,
        report_optional=report,
        warnings=report.warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def render_opportunity_discovery_text(
    report: OpportunityDiscoveryInspectionReport,
) -> str:
    """Render a compact human-readable Stage 0 opportunity view."""
    if not report.opportunity_discovery_present or report.report_optional is None:
        return "\n".join(
            [
                "Opportunity discovery: absent",
                "Publication ready: false",
                *[f"Warning: {warning}" for warning in report.warnings],
            ]
        )
    return render_opportunity_discovery_markdown(report.report_optional).replace("# ", "")


def render_opportunity_discovery_markdown(report: OpportunityDiscoveryReport) -> str:
    """Render one readable Stage 0 opportunity discovery report."""
    promoted = [
        opportunity for opportunity in report.opportunities if opportunity.score_breakdown.promoted
    ]
    rejected = [
        opportunity
        for opportunity in report.opportunities
        if not opportunity.score_breakdown.promoted
    ]
    lines = [
        "# Stage 0 Opportunity Discovery",
        "",
        f"Domain: {report.domain}",
        "",
        "Extracted primitives:",
    ]
    lines.extend(f"- {primitive.name}" for primitive in report.primitives)
    lines.extend(["", "Promoted opportunities:"])
    if promoted:
        for index, opportunity in enumerate(promoted, start=1):
            score = opportunity.score_breakdown
            matched = ", ".join(primitive.name for primitive in opportunity.matched_primitives)
            lines.extend(
                [
                    f"{index}. {opportunity.method_lens.name} / {report.domain}",
                    f"   O_final = {_fmt(score.O_final)}",
                    f"   why: {matched or 'no matched primitives'}; "
                    f"fit={_fmt(score.S_fit)}, verify={_fmt(score.S_verify)}, "
                    f"underuse={_fmt(score.S_underuse)}",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(["", "Rejected:"])
    if rejected:
        for opportunity in rejected[:10]:
            reason = (
                opportunity.score_breakdown.rejection_reason_optional
                or "; ".join(opportunity.false_bridge_reasons)
                or "below promotion threshold"
            )
            lines.append(
                f"- {opportunity.method_lens.name}: {reason}; "
                f"O_final={_fmt(opportunity.score_breakdown.O_final)}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            f"Seed constraints created: {report.seed_constraint_count}",
            "",
            "This report is opportunity-search context only. It does not create "
            "scientific validation, verification evidence, or publication readiness.",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _method_lens(data: dict[str, Any]) -> MethodLens:
    return MethodLens(
        method_id=data["id"],
        name=data["name"],
        method_family=data["family"],
        description=data["description"],
        canonical_objects=data["objects"],
        typical_claim_types=data["claims"],
        typical_baselines=data["baselines"],
        verification_modes=data["verify"],
        data_requirements=data["data"],
        example_domains=data["domains"],
    )


def _opportunity_for_method(
    *,
    run_id: str,
    domain: str,
    method: MethodLens,
    primitives: list[DomainPrimitive],
) -> OpportunityCandidate:
    profile = _profile(method.method_id)
    matched = _matched_primitives(primitives, profile)
    score, false_bridge = _score(
        domain=domain,
        method=method,
        profile=profile,
        matched=matched,
        primitives=primitives,
    )
    primitive_names = [primitive.name for primitive in matched[:3]]
    baseline = method.typical_baselines[0] if method.typical_baselines else "null baseline"
    questions = _possible_questions(domain, method, primitive_names)
    hypotheses = _possible_hypotheses(domain, method, baseline, primitive_names)
    reason = None
    if not score["promoted"]:
        reason = _rejection_reason(score, false_bridge)
    score_breakdown = OpportunityScoreBreakdown(
        S_fit=score["S_fit"],
        S_underuse_raw=score["S_underuse_raw"],
        S_underuse=score["S_underuse"],
        S_abundance=score["S_abundance"],
        S_verify=score["S_verify"],
        S_data=score["S_data"],
        S_paper=score["S_paper"],
        S_forced=score["S_forced"],
        O_raw=score["O_raw"],
        O_final=score["O_final"],
        promoted=score["promoted"],
        rejection_reason_optional=reason,
    )
    return OpportunityCandidate(
        opportunity_id=f"opportunity-{_slug(domain)}-{method.method_id}",
        domain=domain,
        method_lens=method,
        matched_primitives=matched,
        possible_questions=questions,
        possible_hypotheses=hypotheses,
        possible_theory_objects=method.canonical_objects,
        possible_experiment_contracts=_possible_experiment_contracts(
            domain, method, primitive_names, run_id
        ),
        possible_baselines=method.typical_baselines,
        paper_shape=(
            "Domain primitive -> method object -> baseline -> bounded theorem "
            "or synthetic/local experiment -> limitations."
        ),
        risk_flags=_risk_flags(method, score, false_bridge),
        score_breakdown=score_breakdown,
        false_bridge_reasons=false_bridge,
    )


def _score(
    *,
    domain: str,
    method: MethodLens,
    profile: dict[str, Any],
    matched: list[DomainPrimitive],
    primitives: list[DomainPrimitive],
) -> tuple[dict[str, Any], list[str]]:
    match_ratio = len(matched) / max(1, len(primitives))
    domain_family = _domain_family(domain)
    domain_boost = 0.14 if domain_family in method.example_domains else 0.0
    S_fit = _clamp(0.30 + 0.09 * len(matched) + 0.25 * match_ratio + domain_boost)
    S_verify = _verification_score(method)
    S_data = _data_score(method)
    S_abundance = _clamp(0.45 + min(0.25, 0.035 * len(primitives)) + 0.08 * len(matched))
    if method.typical_baselines:
        S_paper = _clamp(0.52 + 0.08 * len(method.typical_baselines) + 0.06 * len(matched))
    else:
        S_paper = 0.35
    S_underuse_raw = float(profile.get("underuse", 0.5))
    S_underuse = S_underuse_raw if S_fit >= 0.70 and S_verify >= 0.60 else 0.0
    false_bridge = _false_bridge_reasons(
        domain=domain,
        method=method,
        matched=matched,
        S_fit=S_fit,
        S_verify=S_verify,
        S_abundance=S_abundance,
    )
    S_forced = _clamp(
        0.0
        + (0.45 if not matched else 0.0)
        + (0.22 if not method.typical_baselines else 0.0)
        + (0.18 if S_verify < 0.60 else 0.0)
        + (0.12 if S_abundance < 0.60 else 0.0)
        + _domain_method_forced_penalty(domain_family, method.method_id)
    )
    O_raw = _clamp(
        0.25 * S_fit
        + 0.20 * S_underuse
        + 0.20 * S_abundance
        + 0.15 * S_verify
        + 0.10 * S_data
        + 0.10 * S_paper
    )
    O_final = _clamp(O_raw - 0.20 * S_forced)
    promoted = O_final >= OPPORTUNITY_THRESHOLD
    return (
        {
            "S_fit": _round(S_fit),
            "S_underuse_raw": _round(S_underuse_raw),
            "S_underuse": _round(S_underuse),
            "S_abundance": _round(S_abundance),
            "S_verify": _round(S_verify),
            "S_data": _round(S_data),
            "S_paper": _round(S_paper),
            "S_forced": _round(S_forced),
            "O_raw": _round(O_raw),
            "O_final": _round(O_final),
            "promoted": promoted,
        },
        false_bridge,
    )


def _matched_primitives(
    primitives: list[DomainPrimitive], profile: dict[str, Any]
) -> list[DomainPrimitive]:
    tags = set(profile["tags"])
    matched = [
        primitive
        for primitive in primitives
        if tags.intersection(primitive.symbolic_tags) or _tokens(primitive.name).intersection(tags)
    ]
    return matched


def _verification_score(method: MethodLens) -> float:
    text = " ".join(method.verification_modes).lower()
    score = 0.45
    if "synthetic" in text:
        score += 0.28
    if "duality" in text or "proof" in text or "formal" in text:
        score += 0.10
    if "diagnostic" in text or "simulation" in text:
        score += 0.08
    return _clamp(score)


def _data_score(method: MethodLens) -> float:
    data = {item.lower() for item in method.data_requirements}
    score = 0.45
    if "synthetic" in data:
        score += 0.28
    if "no data" in data:
        score += 0.18
    if any("public" in item for item in data):
        score += 0.12
    if any("private" in item for item in data):
        score -= 0.25
    return _clamp(score)


def _false_bridge_reasons(
    *,
    domain: str,
    method: MethodLens,
    matched: list[DomainPrimitive],
    S_fit: float,
    S_verify: float,
    S_abundance: float,
) -> list[str]:
    reasons: list[str] = []
    if not matched:
        reasons.append("no primitive-to-object mapping exists")
    if S_fit < 0.55:
        reasons.append("method vocabulary is decorative for the extracted primitives")
    if not method.typical_baselines:
        reasons.append("no baseline exists")
    if S_verify < 0.60:
        reasons.append("no clear theorem or synthetic experiment path exists")
    if S_abundance < 0.60:
        reasons.append("question abundance is low")
    if _domain_method_forced_penalty(_domain_family(domain), method.method_id) >= 0.25:
        reasons.append("forced bridge penalty for this domain-method pair")
    return reasons


def _domain_method_forced_penalty(domain_family: str, method_id: str) -> float:
    forced = {
        ("human geography", "pde_diffusion"): 0.32,
        ("market microstructure", "topological_data_analysis"): 0.24,
        ("option surfaces", "agent_based_modeling"): 0.34,
        ("insurance risk", "pde_diffusion"): 0.24,
        ("robust finance", "agent_based_modeling"): 0.30,
    }
    return forced.get((domain_family, method_id), 0.0)


def _seed_constraint(
    *,
    run_id: str,
    opportunity: OpportunityCandidate,
    index: int,
) -> OpportunitySeedConstraint:
    primitives = [primitive.name for primitive in opportunity.matched_primitives[:5]]
    return OpportunitySeedConstraint(
        seed_id=f"opportunity-seed-{index:04d}-{opportunity.method_lens.method_id}",
        run_id=run_id,
        domain=opportunity.domain,
        method_id=opportunity.method_lens.method_id,
        method_name=opportunity.method_lens.name,
        primitive_ids=[primitive.primitive_id for primitive in opportunity.matched_primitives],
        constraint_fragments={
            "domain": opportunity.domain,
            "method": opportunity.method_lens.name,
            "primitives": primitives,
            "baseline_candidates": opportunity.possible_baselines[:3],
        },
        candidate_generation_hint=(
            f"Seed Stage A with a {opportunity.method_lens.name} branch over "
            f"{', '.join(primitives) or opportunity.domain}; require a concrete "
            "model object, baseline, and bounded verification path."
        ),
        opportunity_id=opportunity.opportunity_id,
        opportunity_score=opportunity.score_breakdown.O_final,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _possible_questions(domain: str, method: MethodLens, primitive_names: list[str]) -> list[str]:
    focus = ", ".join(primitive_names) if primitive_names else domain
    return [
        f"Can {method.name} expose under-modeled structure in {domain} through {focus}?",
        f"Which {method.canonical_objects[0]} gives a bounded, testable lens on {focus}?",
    ]


def _possible_hypotheses(
    domain: str,
    method: MethodLens,
    baseline: str,
    primitive_names: list[str],
) -> list[str]:
    focus = ", ".join(primitive_names) if primitive_names else domain
    return [
        (
            f"A {method.name} representation improves a bounded synthetic check "
            f"over {baseline} for {focus}."
        ),
        (
            f"The {method.name} lens yields a clearer scoped paper claim for "
            f"{domain} than a generic descriptive baseline."
        ),
    ]


def _possible_experiment_contracts(
    domain: str,
    method: MethodLens,
    primitive_names: list[str],
    run_id: str,
) -> list[str]:
    focus = ", ".join(primitive_names) if primitive_names else domain
    return [
        (
            f"{run_id}: synthetic {domain} generator over {focus} with "
            f"{method.typical_baselines[0]} comparison"
        ),
        f"{run_id}: bounded negative-control check for {method.name} on {focus}",
    ]


def _risk_flags(method: MethodLens, score: dict[str, Any], false_bridge: list[str]) -> list[str]:
    flags = list(false_bridge)
    if score["S_underuse"] == 0 and score["S_underuse_raw"] > 0:
        flags.append("underuse signal suppressed because fit or verification is weak")
    if any("public" in item.lower() for item in method.data_requirements):
        flags.append("public-data claims require later retrieval/data scoping")
    return flags


def _rejection_reason(score: dict[str, Any], false_bridge: list[str]) -> str:
    if false_bridge:
        return "; ".join(false_bridge)
    if score["O_final"] < OPPORTUNITY_THRESHOLD:
        return "below opportunity promotion threshold"
    return "not promoted"


def _report_warnings(opportunities: list[OpportunityCandidate]) -> list[str]:
    if not any(opportunity.score_breakdown.promoted for opportunity in opportunities):
        return ["No opportunity exceeded the deterministic promotion threshold."]
    return []


def _domain_family(domain: str) -> str:
    normalized = _normalize(domain)
    for needle, family in _DOMAIN_ALIASES.items():
        if needle in normalized:
            return family
    if normalized in _DOMAIN_PRIMITIVES:
        return normalized
    return "generic fallback domain"


def _fallback_primitives(
    domain: str,
) -> list[tuple[str, str, str, list[str], list[str]]]:
    tokens = [token for token in _tokens(domain) if token not in {"and", "the", "of"}]
    focus = tokens[:3] or ["objects"]
    return [
        (
            "objects",
            "domain_object",
            f"Primary objects in {domain}.",
            ["object", *focus],
            [f"{domain} object table"],
        ),
        (
            "variation",
            "variation_pattern",
            f"Variation or heterogeneity in {domain}.",
            ["variation", "distribution", "metric"],
            [f"{domain} variation measure"],
        ),
        (
            "measurement",
            "measurement_process",
            f"Observable measurements in {domain}.",
            ["measurement", "data", "baseline"],
            [f"{domain} measurement"],
        ),
        (
            "interaction",
            "relation_process",
            f"Relations among {domain} objects.",
            ["network", "interaction", "dependence"],
            [f"{domain} relation graph"],
        ),
        (
            "constraints",
            "constraint_object",
            f"Feasibility or boundary constraints in {domain}.",
            ["constraint", "optimization", "boundary"],
            [f"{domain} constraint set"],
        ),
    ]


def _profile(method_id: str) -> dict[str, Any]:
    for item in _METHOD_DATA:
        if item["id"] == method_id:
            return item
    raise OpportunityDiscoveryError(f"Unknown method lens: {method_id}")


def _latest_report_path(root: Path, run_id: str) -> Path | None:
    reports = root / "runs" / run_id / "reports"
    if not reports.is_dir():
        return None
    matches = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("opportunity-discovery-*.json")
        if (match := _DISCOVERY_RE.fullmatch(path.name))
    )
    return matches[-1][1] if matches else None


def _next_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.glob("opportunity-discovery-*.json")
        if (match := _DISCOVERY_RE.fullmatch(path.name))
    ]
    return (max(numbers) + 1) if numbers else 1


def _normalize(value: str) -> str:
    return " ".join(value.lower().strip().split())


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.lower()))


def _slug(value: str) -> str:
    slug = "-".join(_TOKEN_RE.findall(value.lower()))
    return slug or "unknown"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: float) -> float:
    return round(value, 6)


def _fmt(value: float) -> str:
    return f"{value:.3f}"
