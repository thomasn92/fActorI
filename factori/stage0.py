"""Deterministic fake Stage 0 opportunity discovery."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import persist_markdown_artifact
from factori.reports import render_opportunity_report
from factori.schemas import ArtifactRef, ArtifactType, ConstraintSet, ControllerActionType

OPPORTUNITY_THRESHOLD = 0.70

FAKE_DOMAIN_MAP: dict[str, dict[str, object]] = {
    "human geography": {
        "primitives": ["flows", "distance", "mobility", "spatial inequality"],
        "methods": [
            ("optimal transport", 0.86),
            ("graph curvature", 0.78),
            ("spatial statistics", 0.74),
        ],
    },
    "robust finance": {
        "primitives": ["risk", "drawdown", "stress regimes", "portfolio dispersion"],
        "methods": [
            ("wasserstein robustness", 0.84),
            ("model dispersion", 0.76),
            ("synthetic stress testing", 0.82),
        ],
    },
    "machine learning": {
        "primitives": ["prediction", "calibration", "uncertainty", "shift"],
        "methods": [
            ("calibration", 0.81),
            ("distribution shift", 0.79),
            ("uncertainty quantification", 0.77),
        ],
    },
}

DEFAULT_DOMAIN_ENTRY = {
    "primitives": ["objects", "variation", "measurement"],
    "methods": [
        ("synthetic stress testing", 0.72),
        ("spatial statistics", 0.69),
        ("calibration", 0.68),
    ],
}


@dataclass(frozen=True)
class Stage0Result:
    """Result of deterministic Stage 0 processing."""

    skipped: bool
    input_constraints: ConstraintSet
    seeded_constraints: list[ConstraintSet]
    primitives: list[str]
    opportunities: list[dict[str, object]]
    promoted_methods: list[str]
    report_artifact: ArtifactRef | None
    commit_hash: str


def normalize_domain(domain: str | None) -> str:
    """Normalize a user domain for fake map lookup."""
    return " ".join((domain or "").lower().strip().split())


def fake_primitives(domain: str | None) -> list[str]:
    """Return deterministic fake primitives for a domain."""
    entry = FAKE_DOMAIN_MAP.get(normalize_domain(domain), DEFAULT_DOMAIN_ENTRY)
    return list(entry["primitives"])  # type: ignore[index]


def discover_opportunities(constraints: ConstraintSet) -> list[dict[str, object]]:
    """Return deterministic fake domain-method opportunities."""
    entry = FAKE_DOMAIN_MAP.get(normalize_domain(constraints.domain), DEFAULT_DOMAIN_ENTRY)
    return [
        {
            "domain": constraints.domain,
            "method": method,
            "opportunity_score": score,
            "fake": True,
        }
        for method, score in entry["methods"]  # type: ignore[index]
    ]


def run_stage0(
    *,
    run_id: str,
    constraints: ConstraintSet,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> Stage0Result:
    """Run or skip deterministic Stage 0 and ledger the decision."""
    store.init_run(run_id)
    if not constraints.domain or constraints.method:
        reason = "method already specified" if constraints.method else "domain missing"
        commit = ledger.append_commit(
            run_id=run_id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE0_SKIPPED,
            payload={
                "reason": reason,
                "constraints": constraints.model_dump(mode="json"),
            },
        )
        return Stage0Result(
            skipped=True,
            input_constraints=constraints,
            seeded_constraints=[constraints],
            primitives=list(constraints.primitives),
            opportunities=[],
            promoted_methods=[],
            report_artifact=None,
            commit_hash=commit.commit_hash,
        )

    primitives = fake_primitives(constraints.domain)
    opportunities = discover_opportunities(constraints)
    promoted_methods = [
        str(opportunity["method"])
        for opportunity in opportunities
        if float(opportunity["opportunity_score"]) >= OPPORTUNITY_THRESHOLD
    ]
    seeded_constraints = [
        constraints.model_copy(update={"primitives": primitives, "method": method})
        for method in promoted_methods
    ]

    markdown = render_opportunity_report(
        domain=constraints.domain,
        primitives=primitives,
        opportunities=opportunities,
        promoted_methods=promoted_methods,
    )
    result = persist_markdown_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="stage0-opportunity-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "stage0", "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE0_OPPORTUNITY_DISCOVERY,
        commit_payload={
            "constraints": constraints.model_dump(mode="json"),
            "primitives": primitives,
            "opportunities": opportunities,
            "promoted_methods": promoted_methods,
        },
    )
    return Stage0Result(
        skipped=False,
        input_constraints=constraints,
        seeded_constraints=seeded_constraints,
        primitives=primitives,
        opportunities=opportunities,
        promoted_methods=promoted_methods,
        report_artifact=result.artifact,
        commit_hash=result.commit.commit_hash,
    )
