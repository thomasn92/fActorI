from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import (
    AbstractSynthesisError,
    load_stage_c_results,
    propose_abstract_models,
    run_abstract_synthesis,
    run_abstraction_attack,
    score_abstract_model,
)
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.final_selection import StageCResultItem
from factori.ledger import ResearchLedger
from factori.schemas import (
    AbstractModel,
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    FinalNucleusType,
    InstantiationMap,
    ScoreVector,
    StageCVerificationRecord,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection

HASH = "a" * 64


def test_abstract_synthesis_errors_clearly_if_stage_c_has_not_run(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(AbstractSynthesisError, match="Stage C verification results not found"):
        load_stage_c_results("run-1", ledger)


def test_stage_c_results_are_loaded(tmp_path) -> None:
    store, ledger = _run_pipeline(tmp_path, max_stage_c_candidates=1)

    results = load_stage_c_results("run-1", ledger)

    assert store.run_path("run-1").is_dir()
    assert len(results) == 1
    assert results[0].verification_record.label == VerificationLabel.LEAN_VERIFIED


def test_abstract_model_proposal_and_maps_are_deterministic() -> None:
    stage_c_results = [_item("candidate-a"), _item("candidate-b")]

    first = propose_abstract_models(stage_c_results)
    second = propose_abstract_models(stage_c_results)

    assert first == second
    assert len(first) == 1
    assert first[0].instantiation_maps == second[0].instantiation_maps
    assert all(mapping.coherent for mapping in first[0].instantiation_maps)


def test_abstraction_component_scores_are_deterministic_and_correct() -> None:
    stage_c_results = [
        _item("candidate-a"),
        _item("candidate-b"),
        _item("candidate-c", domain="robust finance"),
    ]
    model = propose_abstract_models(stage_c_results)[0]

    first = score_abstract_model(model, stage_c_results)
    second = score_abstract_model(model, stage_c_results)

    assert first == second
    assert first.coverage == pytest.approx(2 / 3)
    assert first.coherence == pytest.approx(
        sum(mapping.coherence_score for mapping in model.instantiation_maps)
        / len(model.instantiation_maps)
    )
    assert first.compression >= 0.80
    assert first.generativity >= 0.75


def test_verifiability_score_respects_evidence_labels() -> None:
    with_evidence = [_item("candidate-a"), _item("candidate-b")]
    without_evidence = [
        _item("candidate-a", evidence=False),
        _item("candidate-b", evidence=False),
    ]
    with_report = score_abstract_model(propose_abstract_models(with_evidence)[0], with_evidence)
    without_report = score_abstract_model(
        propose_abstract_models(without_evidence)[0],
        without_evidence,
    )

    assert with_report.verifiability > without_report.verifiability


def test_abstraction_attack_is_deterministic_and_accepts_valid_model() -> None:
    stage_c_results = [_item("candidate-a"), _item("candidate-b")]
    model = propose_abstract_models(stage_c_results)[0]

    first = run_abstraction_attack(model, stage_c_results)
    second = run_abstraction_attack(model, stage_c_results)
    report = score_abstract_model(model, stage_c_results)

    assert first == second
    assert first.attack_passed
    assert first.rt_abstract >= first.tau_abstract_redteam
    assert report.accepted_by_score
    assert report.total_score >= report.tau_a


def test_abstraction_attack_rejects_vague_incoherent_and_label_inflating_models() -> None:
    stage_c_results = [_item("candidate-a", label=VerificationLabel.CONJECTURE)]
    bad_map = InstantiationMap(
        id="abstract-bad-to-candidate-a",
        abstract_model_id="abstract-bad",
        candidate_id="candidate-a",
        coherent=False,
        coherence_score=0.20,
        role="instance",
        branch_label=VerificationLabel.CONJECTURE,
        label_preserved=False,
        reason="bad map",
    )
    bad_model = AbstractModel(
        id="abstract-bad",
        objects=["thing"],
        assumptions=["incompatible assumptions"],
        mechanism="vague stuff",
        claim_family="LeanVerified global claim",
        instantiation_maps=[bad_map],
    )

    attack = run_abstraction_attack(bad_model, stage_c_results)

    assert not attack.attack_passed
    assert any("vague" in reason for reason in attack.failure_reasons)
    assert any("incompatible" in reason for reason in attack.failure_reasons)
    assert any("upgrades" in reason for reason in attack.failure_reasons)


def test_labels_synthetic_evidence_and_negative_boundaries_are_preserved() -> None:
    stage_c_results = [
        _item(
            "candidate-synthetic-a",
            label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
            branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
            data_requirement=DataRequirement.SYNTHETIC_ONLY,
        ),
        _item(
            "candidate-synthetic-b",
            label=VerificationLabel.NEGATIVE_RESULT,
            branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
            data_requirement=DataRequirement.SYNTHETIC_ONLY,
        ),
    ]
    model = propose_abstract_models(stage_c_results)[0]
    attack = run_abstraction_attack(model, stage_c_results)

    assert model.synthesis_label == "AbstractSynthesis"
    assert VerificationLabel.LEAN_VERIFIED.value not in model.synthesis_label
    assert VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED.value not in model.claim_family
    assert any(mapping.role == "boundary_case" for mapping in model.instantiation_maps)
    assert not any("negative results" in reason for reason in attack.failure_reasons)


def test_failed_abstraction_falls_back_to_branch_nucleus() -> None:
    result = run_abstract_synthesis_from_items(
        [_item("candidate-a", label=VerificationLabel.CONJECTURE)]
    )

    assert result.final_nucleus.nucleus_type == FinalNucleusType.BRANCH_NUCLEUS
    assert result.final_nucleus.candidate_id == "candidate-a"


def test_run_abstract_synthesis_writes_ledgered_artifacts(tmp_path) -> None:
    store, ledger = _run_pipeline(tmp_path, max_stage_c_candidates=2)

    result = run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)

    assert len(result.stage_c_results) == 2
    assert len(result.abstract_models) == 1
    assert len(result.passing_abstractions) == 1
    assert result.final_nucleus.nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS
    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]
    for action_type in [
        ControllerActionType.ABSTRACT_SYNTHESIS_STARTED,
        ControllerActionType.ABSTRACT_MODEL_PROPOSED,
        ControllerActionType.ABSTRACTION_REPORT_WRITTEN,
        ControllerActionType.ABSTRACTION_ATTACK_RUN,
        ControllerActionType.FINAL_NUCLEUS_SELECTED,
        ControllerActionType.ABSTRACT_SYNTHESIS_REPORT_WRITTEN,
    ]:
        assert action_type in action_types
    assert all(len(artifact.content_hash) == 64 for artifact in result.artifacts)
    assert all(artifact.producing_commit_hash for artifact in result.artifacts)
    assert (tmp_path / result.report_artifact.path).is_file()


def test_cli_synthesize_abstract_works_after_stage_c(tmp_path) -> None:
    runner = CliRunner()
    stage_a = runner.invoke(
        app,
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
    )
    stage_b = runner.invoke(app, ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"])
    select = runner.invoke(
        app,
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    stage_c = runner.invoke(app, ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])
    synthesize = runner.invoke(
        app,
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert select.exit_code == 0
    assert stage_c.exit_code == 0
    assert synthesize.exit_code == 0
    assert "stage_c_results=1" in synthesize.output
    assert "final_nucleus_type=BranchNucleus" in synthesize.output
    assert "abstract_synthesis_report=runs/run-1/reports/abstract-synthesis-report.md" in (
        synthesize.output
    )


def test_cli_synthesize_abstract_errors_without_stage_c(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Stage C verification results not found" in result.stderr


def run_abstract_synthesis_from_items(items: list[StageCResultItem]):
    models = propose_abstract_models(items)
    reports = [score_abstract_model(model, items) for model in models]
    attacks = [run_abstraction_attack(model, items) for model in models]
    from factori.final_selection import select_final_nucleus

    final_nucleus = select_final_nucleus(items, reports, attacks)

    class Result:
        pass

    result = Result()
    result.final_nucleus = final_nucleus
    return result


def _run_pipeline(
    tmp_path,
    *,
    max_stage_c_candidates: int,
) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(
        run_id="run-1",
        store=store,
        ledger=ledger,
        max_stage_c_candidates=max_stage_c_candidates,
    )
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _item(
    candidate_id: str,
    *,
    label: VerificationLabel = VerificationLabel.LEAN_VERIFIED,
    branch_type: BranchVerificationType = BranchVerificationType.MATHEMATICAL,
    data_requirement: DataRequirement = DataRequirement.NO_DATA,
    domain: str = "machine learning",
    evidence: bool = True,
) -> StageCResultItem:
    return StageCResultItem(
        candidate=Candidate(
            id=candidate_id,
            domain=domain,
            method="calibration",
            question="Can calibration branches instantiate a theorem-style abstraction?",
            theory="Theorem-style calibration claim"
            if branch_type == BranchVerificationType.MATHEMATICAL
            else None,
            experiment="Deterministic synthetic contract"
            if data_requirement == DataRequirement.SYNTHETIC_ONLY
            else None,
            data_requirement=data_requirement,
            primitives=["calibration", "uncertainty"],
        ),
        verification_record=StageCVerificationRecord(
            candidate_id=candidate_id,
            branch_type=branch_type,
            label=label,
            status=BranchStatus.STOP_SUCCESS
            if label
            in {
                VerificationLabel.LEAN_VERIFIED,
                VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
            }
            else BranchStatus.ACTIVE,
            evidence_artifacts=[_evidence(label)] if evidence else [],
            reason="test record",
        ),
        stage_c_score=ScoreVector(
            novelty=0.82,
            feasibility=0.84,
            verifiability=0.88,
            reviewer=0.80,
            difficulty=0.30,
            diversity=0.62,
            uncertainty=0.04,
        ),
    )


def _evidence(label: VerificationLabel) -> ArtifactRef:
    if label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        artifact_type = ArtifactType.EXPERIMENT
        path = "runs/run-1/experiments/fake-synthetic-experiment-candidate.json"
        role = "fake_synthetic_experiment"
    else:
        artifact_type = ArtifactType.LEAN
        path = "runs/run-1/lean/fake-proof-candidate.json"
        role = "fake_proof"
    return ArtifactRef(
        id=f"evidence-{label.value}",
        type=artifact_type,
        path=path,
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": role},
    )
