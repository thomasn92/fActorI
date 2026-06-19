"""Deterministic fake Stage A pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.dedup import DedupResult, DuplicateDecision, deduplicate_candidates
from factori.ledger import ResearchLedger
from factori.reports import render_stage_a_report
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    LiteratureState,
    ScoreVector,
)
from factori.scoring import cost_aware_score, passes_stage_a_gate, score_candidate, score_payload
from factori.stage0 import Stage0Result, fake_primitives, run_stage0

MAX_STAGE_A_SURVIVORS = 4


@dataclass(frozen=True)
class StageAResult:
    """Summary of deterministic Stage A execution."""

    run_id: str
    constraints: ConstraintSet
    stage0: Stage0Result
    generated_candidates: list[Candidate]
    deferred_candidates: list[Candidate]
    duplicate_decisions: list[DuplicateDecision]
    gate_pruned_candidates: list[Candidate]
    passing_candidates: list[Candidate]
    survivors: list[Candidate]
    scores: dict[str, ScoreVector]
    candidate_artifacts: dict[str, ArtifactRef]
    score_artifacts: dict[str, ArtifactRef]
    report_artifact: ArtifactRef
    report_commit_hash: str


def constraint_from_inputs(domain: str, method: str | None = None) -> ConstraintSet:
    """Create a simple user constraint from CLI inputs."""
    return ConstraintSet(domain=domain, method=method)


def generate_candidates(seeded_constraints: list[ConstraintSet]) -> list[Candidate]:
    """Generate deterministic fake candidate branches from seeded constraints."""
    candidates: list[Candidate] = []
    for constraint in seeded_constraints:
        domain = constraint.domain or "general research"
        method = constraint.method or "baseline method"
        primitives = list(constraint.primitives) or fake_primitives(domain)
        candidates.extend(_candidate_templates(constraint, domain, method, primitives))
    return candidates


def apply_mvp_data_gate(candidate: Candidate) -> Candidate:
    """Apply the MVP data gate and return a candidate with updated status if needed."""
    if candidate.data_requirement == DataRequirement.PUBLIC_DOWNLOAD:
        return candidate.model_copy(update={"status": BranchStatus.DEFERRED_REAL_DATA_CANDIDATE})
    if candidate.data_requirement == DataRequirement.USER_PROVIDED:
        return candidate.model_copy(update={"status": BranchStatus.REQUIRES_REAL_DATA})
    return candidate


def run_stage_a(
    *,
    run_id: str,
    constraints: ConstraintSet,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> StageAResult:
    """Execute deterministic fake Stage 0 and Stage A."""
    store.init_run(run_id)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_A_STARTED,
        payload={"constraints": constraints.model_dump(mode="json")},
    )

    stage0 = run_stage0(run_id=run_id, constraints=constraints, store=store, ledger=ledger)
    generated_candidates = generate_candidates(stage0.seeded_constraints)
    gated_candidates = [
        _commit_data_gate(run_id, candidate, ledger)
        for candidate in generated_candidates
    ]

    candidate_artifacts = {
        candidate.id: _write_candidate_artifact(run_id, candidate, store, ledger)
        for candidate in gated_candidates
    }

    scores: dict[str, ScoreVector] = {}
    score_artifacts: dict[str, ArtifactRef] = {}
    for candidate in gated_candidates:
        score = score_candidate(candidate)
        scores[candidate.id] = score
        score_artifacts[candidate.id] = _write_score_artifact(
            run_id,
            candidate,
            score,
            store,
            ledger,
        )

    active_candidates = [
        candidate for candidate in gated_candidates if candidate.status == BranchStatus.ACTIVE
    ]
    dedup_result = deduplicate_candidates(active_candidates, scores)
    candidate_by_id = {candidate.id: candidate for candidate in gated_candidates}
    _commit_duplicate_pruning(run_id, dedup_result, candidate_by_id, ledger)

    candidates_after_dedup = [
        candidate_by_id[candidate.id]
        for candidate in dedup_result.kept
        if candidate_by_id[candidate.id].status == BranchStatus.ACTIVE
    ]
    passing_candidates, gate_pruned_candidates = _apply_stage_a_gate(
        run_id,
        candidates_after_dedup,
        scores,
        candidate_by_id,
        ledger,
    )
    survivors = _rank_survivors(passing_candidates, scores)[:MAX_STAGE_A_SURVIVORS]

    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_A_SURVIVORS_SELECTED,
        payload={
            "survivor_ids": [candidate.id for candidate in survivors],
            "passing_candidate_ids": [candidate.id for candidate in passing_candidates],
            "max_survivors": MAX_STAGE_A_SURVIVORS,
        },
    )

    deferred_candidates = [
        candidate_by_id[candidate.id]
        for candidate in gated_candidates
        if candidate_by_id[candidate.id].status
        in {BranchStatus.DEFERRED_REAL_DATA_CANDIDATE, BranchStatus.REQUIRES_REAL_DATA}
    ]
    report_artifact, report_commit_hash = _write_stage_a_report(
        run_id=run_id,
        generated_candidates=list(candidate_by_id.values()),
        deferred_candidates=deferred_candidates,
        duplicate_decisions=dedup_result.pruned,
        gate_pruned_candidates=gate_pruned_candidates,
        passing_candidates=passing_candidates,
        survivors=survivors,
        scores=scores,
        store=store,
        ledger=ledger,
    )

    return StageAResult(
        run_id=run_id,
        constraints=constraints,
        stage0=stage0,
        generated_candidates=list(candidate_by_id.values()),
        deferred_candidates=deferred_candidates,
        duplicate_decisions=dedup_result.pruned,
        gate_pruned_candidates=gate_pruned_candidates,
        passing_candidates=passing_candidates,
        survivors=survivors,
        scores=scores,
        candidate_artifacts=candidate_artifacts,
        score_artifacts=score_artifacts,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
    )


def _candidate_templates(
    constraint: ConstraintSet,
    domain: str,
    method: str,
    primitives: list[str],
) -> list[Candidate]:
    slug = f"{_slug(domain)}-{_slug(method)}"
    base_question = f"Can {method} expose structure in {domain}?"
    base_hypothesis = f"{method} reveals a verifiable structure over {', '.join(primitives[:2])}."
    base_theory = f"Define a {method} lens over {domain} primitives."
    base_baseline = f"Compare against a direct descriptive baseline for {domain}."
    fake_literature = LiteratureState(
        k=5,
        semantic=0.82,
        keyword=0.80,
        citation=0.78,
        diversity=0.76,
        adversarial=0.74,
        novelty_risk=0.18,
        closest_priors=["fake-prior-001", "fake-prior-002"],
    )
    template_specs = [
        (
            "theory",
            DataRequirement.NO_DATA,
            base_question,
            base_hypothesis,
            base_theory,
            None,
        ),
        (
            "synthetic",
            DataRequirement.SYNTHETIC_ONLY,
            f"Can synthetic regimes test {method} on {domain}?",
            f"Controlled simulations expose when {method} is stable for {domain}.",
            base_theory,
            "Predeclare a deterministic synthetic generator and baseline comparison.",
        ),
        (
            "public-data",
            DataRequirement.PUBLIC_DOWNLOAD,
            f"Can public datasets validate {method} for {domain}?",
            f"External public data would test the proposed {method} mechanism.",
            base_theory,
            "Requires public dataset licensing and reproducibility checks.",
        ),
        (
            "user-data",
            DataRequirement.USER_PROVIDED,
            f"Can private user data validate {method} for {domain}?",
            f"User-provided data would test whether {method} transfers to private cases.",
            base_theory,
            "Requires user-provided private or manually curated data.",
        ),
        (
            "duplicate",
            DataRequirement.NO_DATA,
            base_question,
            base_hypothesis,
            base_theory,
            None,
        ),
    ]

    candidates = []
    for key, data_requirement, question, hypothesis, theory, experiment in template_specs:
        candidate_constraints = constraint.model_copy(
            update={
                "primitives": primitives,
                "question": question,
                "hypothesis": hypothesis,
                "theory": theory,
                "experiment": experiment,
                "baseline": base_baseline,
                "data_requirement": data_requirement,
            }
        )
        candidates.append(
            Candidate(
                id=f"cand-{slug}-{key}",
                constraints=candidate_constraints,
                domain=domain,
                primitives=primitives,
                method=method,
                question=question,
                hypothesis=hypothesis,
                theory=theory,
                experiment=experiment,
                baseline=base_baseline,
                data_requirement=data_requirement,
                literature=fake_literature,
                symbolic_state={
                    "objects": primitives,
                    "method_class": method,
                    "target_directive": "deterministic_fake_stage_a",
                    "fake": True,
                },
            )
        )
    return candidates


def _commit_data_gate(
    run_id: str,
    candidate: Candidate,
    ledger: ResearchLedger,
) -> Candidate:
    gated = apply_mvp_data_gate(candidate)
    if gated.status != candidate.status:
        ledger.append_commit(
            run_id=run_id,
            candidate_id=candidate.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_A_DATA_GATE_DEFERRED,
            payload={
                "candidate_id": candidate.id,
                "data_requirement": candidate.data_requirement.value,
                "from_status": candidate.status.value,
                "to_status": gated.status.value,
            },
        )
    return gated


def _write_candidate_artifact(
    run_id: str,
    candidate: Candidate,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=candidate.id,
        artifact_type=ArtifactType.CANDIDATE,
        data=candidate,
        metadata={"stage": "stage_a", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_A_CANDIDATE_GENERATED,
        payload={"candidate": candidate.model_dump(mode="json")},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_score_artifact(
    run_id: str,
    candidate: Candidate,
    score: ScoreVector,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"{candidate.id}-score",
        artifact_type=ArtifactType.SCORE,
        data=score_payload(candidate, score),
        metadata={"stage": "stage_a", "candidate_id": candidate.id, "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_A_SCORE_COMPUTED,
        payload=score_payload(candidate, score),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _commit_duplicate_pruning(
    run_id: str,
    dedup_result: DedupResult,
    candidate_by_id: dict[str, Candidate],
    ledger: ResearchLedger,
) -> None:
    for decision in dedup_result.pruned:
        candidate = candidate_by_id[decision.candidate_id]
        pruned = candidate.model_copy(update={"status": BranchStatus.PRUNED_DUPLICATE})
        candidate_by_id[decision.candidate_id] = pruned
        ledger.append_commit(
            run_id=run_id,
            candidate_id=decision.candidate_id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_A_DUPLICATE_PRUNED,
            payload={
                "candidate_id": decision.candidate_id,
                "duplicate_of": decision.duplicate_of,
                "distance": decision.distance,
                "from_status": candidate.status.value,
                "to_status": pruned.status.value,
            },
        )


def _apply_stage_a_gate(
    run_id: str,
    candidates: list[Candidate],
    scores: dict[str, ScoreVector],
    candidate_by_id: dict[str, Candidate],
    ledger: ResearchLedger,
) -> tuple[list[Candidate], list[Candidate]]:
    passing: list[Candidate] = []
    pruned: list[Candidate] = []
    for candidate in candidates:
        score = scores[candidate.id]
        if passes_stage_a_gate(score):
            passing.append(candidate)
            continue
        updated = candidate.model_copy(update={"status": BranchStatus.PRUNED_UNCERTAIN})
        candidate_by_id[candidate.id] = updated
        pruned.append(updated)
        ledger.append_commit(
            run_id=run_id,
            candidate_id=candidate.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_A_GATE_PRUNED,
            payload={
                "candidate_id": candidate.id,
                "score": score.model_dump(mode="json"),
                "from_status": candidate.status.value,
                "to_status": updated.status.value,
                "gate": {
                    "novelty_min": 0.35,
                    "feasibility_min": 0.60,
                    "verifiability_min": 0.50,
                },
            },
        )
    return passing, pruned


def _rank_survivors(
    candidates: list[Candidate],
    scores: dict[str, ScoreVector],
) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (-cost_aware_score(candidate, scores[candidate.id]), candidate.id),
    )


def _write_stage_a_report(
    *,
    run_id: str,
    generated_candidates: list[Candidate],
    deferred_candidates: list[Candidate],
    duplicate_decisions: list[DuplicateDecision],
    gate_pruned_candidates: list[Candidate],
    passing_candidates: list[Candidate],
    survivors: list[Candidate],
    scores: dict[str, ScoreVector],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, str]:
    markdown = render_stage_a_report(
        run_id=run_id,
        generated_candidates=generated_candidates,
        deferred_candidates=deferred_candidates,
        duplicate_decisions=duplicate_decisions,
        gate_pruned_candidates=gate_pruned_candidates,
        passing_candidates=passing_candidates,
        survivors=survivors,
        scores=scores,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="stage-a-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "stage_a", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_A_REPORT_WRITTEN,
        payload={
            "generated_count": len(generated_candidates),
            "deferred_count": len(deferred_candidates),
            "duplicate_pruned_count": len(duplicate_decisions),
            "passing_stage_a_count": len(passing_candidates),
            "survivor_ids": [candidate.id for candidate in survivors],
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash


def _slug(value: str) -> str:
    slug = "-".join("".join(char if char.isalnum() else " " for char in value.lower()).split())
    return slug or "unspecified"
