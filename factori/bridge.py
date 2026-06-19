"""Deterministic fake bridge checks for Stage B."""

from __future__ import annotations

from factori.schemas import BranchStatus, BridgeRepairAction, BridgeReport, Candidate

BRIDGE_THRESHOLD = 0.70


def compute_bridge_survival_score(
    *,
    map_score: float,
    transfer_score: float,
    baseline_score: float,
    data_score: float,
    falsify_score: float,
    nondecorative_score: float,
) -> float:
    """Return the weighted bridge survival score from the specification."""
    return round(
        0.25 * map_score
        + 0.20 * transfer_score
        + 0.20 * baseline_score
        + 0.15 * data_score
        + 0.10 * falsify_score
        + 0.10 * nondecorative_score,
        6,
    )


def run_bridge_check(candidate: Candidate) -> BridgeReport:
    """Run a deterministic bridge check and at most one repair."""
    initial = _bridge_components(candidate)
    initial_score = compute_bridge_survival_score(**initial)
    if initial_score >= BRIDGE_THRESHOLD:
        return _report(candidate.id, initial, initial_score, survives=True)

    repair_action = choose_bridge_repair(candidate, initial)
    if repair_action == BridgeRepairAction.REJECT_BRIDGE:
        return _report(
            candidate.id,
            initial,
            initial_score,
            survives=False,
            repair_attempted=True,
            repair_action=repair_action,
            final_status=BranchStatus.REJECTED_RED_TEAM,
        )

    repaired = _apply_bridge_repair(initial, repair_action)
    repaired_score = compute_bridge_survival_score(**repaired)
    return _report(
        candidate.id,
        repaired,
        repaired_score,
        survives=repaired_score >= BRIDGE_THRESHOLD,
        repair_attempted=True,
        repair_action=repair_action,
        final_status=BranchStatus.ACTIVE
        if repaired_score >= BRIDGE_THRESHOLD
        else BranchStatus.FALSE_BRIDGE,
    )


def choose_bridge_repair(
    candidate: Candidate,
    components: dict[str, float],
) -> BridgeRepairAction:
    """Choose one deterministic bridge repair action."""
    if "decorative" in candidate.id:
        return BridgeRepairAction.REJECT_BRIDGE
    weakest = min(components, key=components.get)
    return {
        "map_score": BridgeRepairAction.DEFINE_OBJECTS,
        "transfer_score": BridgeRepairAction.CHANGE_METRIC,
        "baseline_score": BridgeRepairAction.ADD_BASELINE,
        "data_score": BridgeRepairAction.ADD_SYNTHETIC_DATA,
        "falsify_score": BridgeRepairAction.NARROW_CLAIM,
        "nondecorative_score": BridgeRepairAction.REPLACE_METHOD,
    }[weakest]


def _bridge_components(candidate: Candidate) -> dict[str, float]:
    variant_type = str(candidate.symbolic_state.get("variant_type", "narrow_scope"))
    components = {
        "map_score": 0.73,
        "transfer_score": 0.70,
        "baseline_score": 0.69,
        "data_score": 0.72,
        "falsify_score": 0.71,
        "nondecorative_score": 0.74,
    }
    if variant_type == "narrow_scope":
        components["map_score"] += 0.06
        components["falsify_score"] += 0.04
    elif variant_type == "stronger_baseline":
        components["baseline_score"] += 0.08
    elif variant_type == "synthetic_experiment_contract":
        components["data_score"] += 0.08
        components["falsify_score"] += 0.03
    elif variant_type == "theorem_or_conjecture_form":
        components["map_score"] += 0.03
        components["nondecorative_score"] += 0.04

    if "false-bridge" in candidate.id:
        components.update(
            {
                "map_score": 0.32,
                "transfer_score": 0.35,
                "baseline_score": 0.40,
                "data_score": 0.42,
                "falsify_score": 0.38,
                "nondecorative_score": 0.34,
            }
        )
    return {key: _clamp(value) for key, value in components.items()}


def _apply_bridge_repair(
    components: dict[str, float],
    repair_action: BridgeRepairAction,
) -> dict[str, float]:
    repaired = dict(components)
    repair_targets = {
        BridgeRepairAction.DEFINE_OBJECTS: "map_score",
        BridgeRepairAction.CHANGE_METRIC: "transfer_score",
        BridgeRepairAction.ADD_BASELINE: "baseline_score",
        BridgeRepairAction.ADD_SYNTHETIC_DATA: "data_score",
        BridgeRepairAction.NARROW_CLAIM: "falsify_score",
        BridgeRepairAction.REPLACE_METHOD: "nondecorative_score",
    }
    target = repair_targets[repair_action]
    repaired[target] = _clamp(repaired[target] + 0.18)
    return repaired


def _report(
    candidate_id: str,
    components: dict[str, float],
    survival_score: float,
    *,
    survives: bool,
    repair_attempted: bool = False,
    repair_action: BridgeRepairAction | None = None,
    final_status: BranchStatus = BranchStatus.ACTIVE,
) -> BridgeReport:
    return BridgeReport(
        candidate_id=candidate_id,
        **components,
        survival_score=survival_score,
        survives=survives,
        repair_attempted=repair_attempted,
        repair_action=repair_action,
        final_status=final_status,
    )


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
