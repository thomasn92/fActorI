from __future__ import annotations

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import ConstraintSet, ControllerActionType
from factori.stage0 import discover_opportunities, run_stage0


def test_stage0_runs_when_domain_exists_and_method_missing(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage0(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )

    assert not result.skipped
    assert result.promoted_methods == [
        "optimal transport",
        "graph curvature",
        "spatial statistics",
    ]
    assert result.report_artifact is not None
    assert (tmp_path / result.report_artifact.path).is_file()
    assert ledger.list_commits("run-1")[-1].action_type == (
        ControllerActionType.STAGE0_OPPORTUNITY_DISCOVERY
    )


def test_stage0_skips_when_method_is_provided(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    constraints = ConstraintSet(domain="human geography", method="optimal transport")

    result = run_stage0(
        run_id="run-1",
        constraints=constraints,
        store=store,
        ledger=ledger,
    )

    assert result.skipped
    assert result.seeded_constraints == [constraints]
    assert result.report_artifact is None
    assert ledger.list_commits("run-1")[-1].action_type == ControllerActionType.STAGE0_SKIPPED


def test_opportunity_discovery_is_deterministic() -> None:
    constraints = ConstraintSet(domain="robust finance")

    first = discover_opportunities(constraints)
    second = discover_opportunities(constraints)

    assert first == second
    assert first[0]["method"] == "wasserstein robustness"
    assert first[0]["opportunity_score"] == 0.84
