"""Deterministic Abstract Synthesis skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.final_selection import StageCResultItem, select_final_nucleus
from factori.ledger import ResearchLedger
from factori.reports import render_abstract_synthesis_report
from factori.schemas import (
    AbstractionAttackReport,
    AbstractionReport,
    AbstractModel,
    ArtifactRef,
    ArtifactType,
    Candidate,
    ControllerActionType,
    FinalNucleus,
    InstantiationMap,
    ScoreVector,
    StageCVerificationRecord,
    VerificationLabel,
)

TAU_A = 0.75
TAU_ABSTRACT_REDTEAM = 0.75


class AbstractSynthesisError(RuntimeError):
    """Raised when abstract synthesis prerequisites are missing."""


@dataclass(frozen=True)
class AbstractSynthesisResult:
    """Result of deterministic abstract synthesis."""

    run_id: str
    stage_c_results: list[StageCResultItem]
    abstract_models: list[AbstractModel]
    abstraction_reports: list[AbstractionReport]
    attack_reports: list[AbstractionAttackReport]
    passing_abstractions: list[AbstractionReport]
    final_nucleus: FinalNucleus
    artifacts: list[ArtifactRef]
    final_nucleus_artifact: ArtifactRef
    report_artifact: ArtifactRef
    report_commit_hash: str


def load_stage_c_results(run_id: str, ledger: ResearchLedger) -> list[StageCResultItem]:
    """Load latest Stage C verification decisions and candidate payloads."""
    commits = ledger.list_commits(run_id)
    has_stage_c_report = any(
        commit.action_type == ControllerActionType.STAGE_C_VERIFICATION_REPORT_WRITTEN
        for commit in commits
    )
    if not has_stage_c_report:
        raise AbstractSynthesisError(
            "Stage C verification results not found; run factori run-stage-c first"
        )

    candidates: dict[str, Candidate] = {}
    scores: dict[str, ScoreVector] = {}
    records: dict[str, StageCVerificationRecord] = {}
    for commit in commits:
        candidate_id = commit.candidate_id
        if commit.action_type == ControllerActionType.STAGE_B_CHILD_GENERATED:
            payload = commit.payload.get("candidate")
            if payload is not None:
                candidate = Candidate.model_validate(payload)
                candidates[candidate.id] = candidate
        elif commit.action_type == ControllerActionType.STAGE_C_SCORE_COMPUTED and candidate_id:
            scores[candidate_id] = ScoreVector.model_validate(commit.payload["score"])
        elif commit.action_type == ControllerActionType.STAGE_C_VERIFICATION_DECIDED:
            record = StageCVerificationRecord.model_validate(commit.payload)
            records[record.candidate_id] = record

    if not records:
        raise AbstractSynthesisError(
            "Stage C verification decisions not found; run factori run-stage-c first"
        )

    missing = [candidate_id for candidate_id in records if candidate_id not in candidates]
    if missing:
        raise AbstractSynthesisError(
            "Stage C candidate payloads missing: " + ", ".join(sorted(missing))
        )

    return [
        StageCResultItem(
            candidate=candidates[candidate_id],
            verification_record=records[candidate_id],
            stage_c_score=scores.get(candidate_id),
        )
        for candidate_id in sorted(records)
    ]


def propose_abstract_models(stage_c_results: list[StageCResultItem]) -> list[AbstractModel]:
    """Propose deterministic abstract models from Stage C results."""
    groups: dict[tuple[str, str], list[StageCResultItem]] = {}
    for item in stage_c_results:
        domain = _safe_token(item.candidate.domain or "general")
        claim_type = _claim_type(item)
        groups.setdefault((domain, claim_type), []).append(item)

    models: list[AbstractModel] = []
    for (domain, claim_type), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        model_id = f"abstract-{domain}-{claim_type}"
        objects = _abstract_objects(items)
        assumptions = _abstract_assumptions(items)
        mechanism = _abstract_mechanism(items)
        claim_family = _abstract_claim_family(items)
        maps = [
            _instantiation_map(model_id, item, objects, claim_family)
            for item in sorted(items, key=lambda result: result.candidate.id)
        ]
        models.append(
            AbstractModel(
                id=model_id,
                objects=objects,
                assumptions=assumptions,
                mechanism=mechanism,
                claim_family=claim_family,
                instantiation_maps=maps,
            )
        )
    return models


def score_abstract_model(
    model: AbstractModel,
    stage_c_results: list[StageCResultItem],
    *,
    tau_a: float = TAU_A,
) -> AbstractionReport:
    """Score a proposed abstraction with the deterministic MVP formula."""
    if not stage_c_results:
        coverage = 0.0
    else:
        coverage = round(_valid_map_count(model) / len(stage_c_results), 6)
    coherence = _coherence_score(model)
    compression = _compression_score(model)
    generativity = _generativity_score(model)
    verifiability = _verifiability_score(model, stage_c_results)
    total_score = round(
        0.25 * coverage
        + 0.25 * coherence
        + 0.20 * compression
        + 0.15 * generativity
        + 0.15 * verifiability,
        6,
    )
    return AbstractionReport(
        abstract_model_id=model.id,
        model=model,
        coverage=coverage,
        coherence=coherence,
        compression=compression,
        generativity=generativity,
        verifiability=verifiability,
        total_score=total_score,
        tau_a=tau_a,
        accepted_by_score=total_score >= tau_a,
        branch_ids=[mapping.candidate_id for mapping in model.instantiation_maps],
    )


def run_abstraction_attack(
    model: AbstractModel,
    branches: list[StageCResultItem],
    *,
    tau_abstract_redteam: float = TAU_ABSTRACT_REDTEAM,
) -> AbstractionAttackReport:
    """Run deterministic red-team checks against an abstraction."""
    branch_by_id = {item.candidate.id: item for item in branches}
    failure_reasons: list[str] = []
    if len(model.instantiation_maps) < 2:
        failure_reasons.append("abstraction does not cover multiple branches")
    if any(not mapping.coherent for mapping in model.instantiation_maps):
        failure_reasons.append("one or more mapped branches are not coherent instances")
    if any(mapping.candidate_id not in branch_by_id for mapping in model.instantiation_maps):
        failure_reasons.append("mapped branch is missing from Stage C results")
    if _has_incompatible_assumptions(model):
        failure_reasons.append("assumptions are incompatible")
    if _is_vague(model):
        failure_reasons.append("abstraction is vague vocabulary rather than a model")
    if _compression_score(model) < 0.70:
        failure_reasons.append("abstraction does not compress the branch structure")
    if any(not mapping.label_preserved for mapping in model.instantiation_maps):
        failure_reasons.append("verification labels are not preserved")
    if _inflates_labels(model, branch_by_id):
        failure_reasons.append("abstraction upgrades labeled branch claims")
    if _misuses_negative_results(model):
        failure_reasons.append("negative results are treated as positive evidence")

    rt_abstract = round(max(0.0, 1.0 - 0.13 * len(failure_reasons)), 6)
    return AbstractionAttackReport(
        abstract_model_id=model.id,
        rt_abstract=rt_abstract,
        tau_abstract_redteam=tau_abstract_redteam,
        attack_passed=rt_abstract >= tau_abstract_redteam and not failure_reasons,
        failure_reasons=failure_reasons,
    )


def run_abstract_synthesis(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> AbstractSynthesisResult:
    """Run deterministic abstract synthesis and final nucleus selection."""
    store.init_run(run_id)
    stage_c_results = load_stage_c_results(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.ABSTRACT_SYNTHESIS_STARTED,
        payload={"stage_c_result_ids": [item.candidate.id for item in stage_c_results]},
    )

    artifacts: list[ArtifactRef] = []
    models = propose_abstract_models(stage_c_results)
    reports: list[AbstractionReport] = []
    attacks: list[AbstractionAttackReport] = []
    for model in models:
        ledger.append_commit(
            run_id=run_id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.ABSTRACT_MODEL_PROPOSED,
            payload=model.model_dump(mode="json"),
        )
        report = score_abstract_model(model, stage_c_results)
        reports.append(report)
        artifacts.append(_write_abstraction_report(run_id, report, store, ledger))
        attack = run_abstraction_attack(model, stage_c_results)
        attacks.append(attack)
        artifacts.append(_write_abstraction_attack(run_id, attack, store, ledger))

    passing = [
        report
        for report in reports
        if report.accepted_by_score
        and any(
            attack.abstract_model_id == report.abstract_model_id and attack.attack_passed
            for attack in attacks
        )
    ]
    final_nucleus = select_final_nucleus(stage_c_results, reports, attacks)
    final_nucleus_artifact = _write_final_nucleus(
        run_id,
        final_nucleus,
        store,
        ledger,
    )
    artifacts.append(final_nucleus_artifact)
    report_artifact, report_commit_hash = _write_synthesis_report(
        run_id=run_id,
        stage_c_results=stage_c_results,
        abstraction_reports=reports,
        attack_reports=attacks,
        passing_abstractions=passing,
        final_nucleus=final_nucleus,
        store=store,
        ledger=ledger,
    )
    artifacts.append(report_artifact)
    return AbstractSynthesisResult(
        run_id=run_id,
        stage_c_results=stage_c_results,
        abstract_models=models,
        abstraction_reports=reports,
        attack_reports=attacks,
        passing_abstractions=passing,
        final_nucleus=final_nucleus,
        artifacts=artifacts,
        final_nucleus_artifact=final_nucleus_artifact,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
    )


def _write_abstraction_report(
    run_id: str,
    report: AbstractionReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"abstraction-report-{report.abstract_model_id}",
        artifact_type=ArtifactType.REPORT,
        data=report,
        metadata={"stage": "abstract_synthesis", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.ABSTRACTION_REPORT_WRITTEN,
        payload=report.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_abstraction_attack(
    run_id: str,
    attack: AbstractionAttackReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"abstraction-attack-{attack.abstract_model_id}",
        artifact_type=ArtifactType.REPORT,
        data=attack,
        metadata={"stage": "abstract_synthesis", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.ABSTRACTION_ATTACK_RUN,
        payload=attack.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_final_nucleus(
    run_id: str,
    final_nucleus: FinalNucleus,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="final-nucleus",
        artifact_type=ArtifactType.REPORT,
        data=final_nucleus,
        metadata={"stage": "abstract_synthesis", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.FINAL_NUCLEUS_SELECTED,
        payload=final_nucleus.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_synthesis_report(
    *,
    run_id: str,
    stage_c_results: list[StageCResultItem],
    abstraction_reports: list[AbstractionReport],
    attack_reports: list[AbstractionAttackReport],
    passing_abstractions: list[AbstractionReport],
    final_nucleus: FinalNucleus,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, str]:
    markdown = render_abstract_synthesis_report(
        run_id=run_id,
        stage_c_results=stage_c_results,
        abstraction_reports=abstraction_reports,
        attack_reports=attack_reports,
        passing_abstractions=passing_abstractions,
        final_nucleus=final_nucleus,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="abstract-synthesis-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "abstract_synthesis", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.ABSTRACT_SYNTHESIS_REPORT_WRITTEN,
        payload={
            "stage_c_results": len(stage_c_results),
            "abstract_models_proposed": len(abstraction_reports),
            "abstract_models_passed": len(passing_abstractions),
            "final_nucleus_type": final_nucleus.nucleus_type.value,
            "final_nucleus_id": final_nucleus.id,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash


def _instantiation_map(
    model_id: str,
    item: StageCResultItem,
    objects: list[str],
    claim_family: str,
) -> InstantiationMap:
    candidate_terms = _candidate_terms(item.candidate)
    shared_terms = set(objects) & candidate_terms
    coherence_score = round(
        min(1.0, 0.55 + 0.15 * len(shared_terms) + _label_coherence_bonus(item)),
        6,
    )
    label_preserved = item.verification_record.label.value in claim_family
    role = (
        "boundary_case"
        if item.verification_record.label == VerificationLabel.NEGATIVE_RESULT
        else "instance"
    )
    return InstantiationMap(
        id=f"{model_id}-to-{item.candidate.id}",
        abstract_model_id=model_id,
        candidate_id=item.candidate.id,
        coherent=coherence_score >= 0.70,
        coherence_score=coherence_score,
        role=role,
        branch_label=item.verification_record.label,
        label_preserved=label_preserved,
        reason="deterministic map preserves branch label and shared objects",
    )


def _abstract_objects(items: list[StageCResultItem]) -> list[str]:
    term_sets = [_candidate_terms(item.candidate) for item in items]
    shared = set.intersection(*term_sets) if term_sets else set()
    if not shared:
        shared = set.union(*term_sets) if term_sets else {"research-object"}
    return sorted(shared)[:5] or ["research-object"]


def _abstract_assumptions(items: list[StageCResultItem]) -> list[str]:
    data_regimes = sorted({item.candidate.data_requirement.value for item in items})
    labels = sorted({item.verification_record.label.value for item in items})
    return [
        "instances keep their original verification labels",
        "negative results are boundary cases",
        "data regimes: " + ", ".join(data_regimes),
        "labels: " + ", ".join(labels),
    ]


def _abstract_mechanism(items: list[StageCResultItem]) -> str:
    methods = sorted({item.candidate.method for item in items if item.candidate.method})
    if methods:
        return "shared deterministic mechanism over " + ", ".join(methods[:3])
    return "shared deterministic mechanism over labeled branch structure"


def _abstract_claim_family(items: list[StageCResultItem]) -> str:
    labels = sorted({item.verification_record.label.value for item in items})
    return "AbstractSynthesis preserving " + ", ".join(labels)


def _claim_type(item: StageCResultItem) -> str:
    if item.verification_record.branch_type.value == "SyntheticEmpirical":
        return "synthetic"
    if item.verification_record.branch_type.value == "NoDataMethodological":
        return "methodological"
    if "theorem" in _candidate_text(item.candidate) or item.verification_record.label == (
        VerificationLabel.LEAN_VERIFIED
    ):
        return "mathematical"
    if item.candidate.data_requirement.value == "SyntheticOnly":
        return "synthetic"
    return "methodological"


def _valid_map_count(model: AbstractModel) -> int:
    return sum(1 for mapping in model.instantiation_maps if mapping.coherent)


def _coherence_score(model: AbstractModel) -> float:
    if not model.instantiation_maps:
        return 0.0
    return round(
        sum(mapping.coherence_score for mapping in model.instantiation_maps)
        / len(model.instantiation_maps),
        6,
    )


def _compression_score(model: AbstractModel) -> float:
    mapped = len(model.instantiation_maps)
    if mapped < 2:
        return 0.20
    penalty = 0.15 if _is_vague(model) else 0.0
    return round(min(1.0, max(0.0, 0.68 + 0.10 * min(mapped, 4) - penalty)), 6)


def _generativity_score(model: AbstractModel) -> float:
    labels = {mapping.branch_label for mapping in model.instantiation_maps}
    has_boundary = any(mapping.role == "boundary_case" for mapping in model.instantiation_maps)
    score = 0.62 + 0.08 * min(3, len(model.instantiation_maps))
    if len(labels) >= 2:
        score += 0.06
    if has_boundary:
        score += 0.04
    return round(min(1.0, score), 6)


def _verifiability_score(
    model: AbstractModel,
    stage_c_results: list[StageCResultItem],
) -> float:
    records = {
        item.candidate.id: item.verification_record
        for item in stage_c_results
    }
    scores: list[float] = []
    for mapping in model.instantiation_maps:
        record = records.get(mapping.candidate_id)
        if record is None:
            scores.append(0.0)
        elif record.label == VerificationLabel.LEAN_VERIFIED:
            scores.append(1.0 if record.evidence_artifacts else 0.75)
        elif record.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
            scores.append(0.88 if record.evidence_artifacts else 0.65)
        elif record.label == VerificationLabel.NEGATIVE_RESULT:
            scores.append(0.62)
        elif record.label == VerificationLabel.CONJECTURE:
            scores.append(0.48)
        elif record.label == VerificationLabel.LIMITATION:
            scores.append(0.40)
        else:
            scores.append(0.10)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 6)


def _has_incompatible_assumptions(model: AbstractModel) -> bool:
    text = " ".join(model.assumptions).lower()
    return "incompatible" in text or ("requires real data" in text and "no data" in text)


def _is_vague(model: AbstractModel) -> bool:
    text = " ".join([*model.objects, model.mechanism, model.claim_family]).lower()
    vague_terms = {"thing", "stuff", "interesting", "misc", "vague"}
    return not model.objects or any(term in text for term in vague_terms)


def _inflates_labels(
    model: AbstractModel,
    branch_by_id: dict[str, StageCResultItem],
) -> bool:
    if model.synthesis_label in {
        VerificationLabel.LEAN_VERIFIED.value,
        VerificationLabel.EXPERIMENT_VERIFIED.value,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED.value,
        VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED.value,
    }:
        return True
    claim_text = model.claim_family.lower()
    for mapping in model.instantiation_maps:
        item = branch_by_id.get(mapping.candidate_id)
        if item is None:
            continue
        label = item.verification_record.label
        if label != VerificationLabel.LEAN_VERIFIED and "leanverified" in claim_text:
            return True
        if (
            label != VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
            and "syntheticexperimentverified" in claim_text
        ):
            return True
    return False


def _misuses_negative_results(model: AbstractModel) -> bool:
    return any(
        mapping.branch_label == VerificationLabel.NEGATIVE_RESULT
        and mapping.role != "boundary_case"
        for mapping in model.instantiation_maps
    )


def _label_coherence_bonus(item: StageCResultItem) -> float:
    if item.verification_record.label in {
        VerificationLabel.LEAN_VERIFIED,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
    }:
        return 0.10
    if item.verification_record.label == VerificationLabel.NEGATIVE_RESULT:
        return 0.05
    if item.verification_record.label == VerificationLabel.CONJECTURE:
        return 0.03
    return 0.0


def _candidate_terms(candidate: Candidate) -> set[str]:
    terms = set(candidate.primitives)
    for value in [
        candidate.domain,
        candidate.method,
        candidate.question,
        candidate.hypothesis,
        candidate.theory,
        candidate.experiment,
        candidate.variant_type,
    ]:
        if value:
            terms.update(_tokenize(value))
    terms.update(_tokenize(" ".join(str(value) for value in candidate.symbolic_state.values())))
    return {term for term in terms if len(term) >= 4}


def _candidate_text(candidate: Candidate) -> str:
    return " ".join(
        value
        for value in [
            candidate.id,
            candidate.domain or "",
            candidate.method or "",
            candidate.question,
            candidate.hypothesis or "",
            candidate.theory or "",
            candidate.experiment or "",
            candidate.variant_type or "",
        ]
        if value
    ).lower()


def _tokenize(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    stop = {
        "with",
        "this",
        "that",
        "candidate",
        "branch",
        "under",
        "over",
        "form",
        "fake",
        "stage",
    }
    return {token for token in normalized.split() if token not in stop}


def _safe_token(value: str) -> str:
    tokens = sorted(_tokenize(value))
    return "-".join(tokens[:3]) or "general"
