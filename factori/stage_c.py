"""Deterministic fake Stage C verification skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.evidence import (
    PROOF_EVIDENCE_ROLE,
    SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
    claim_label_allowed,
)
from factori.experiments_fake import run_fake_synthetic_experiment
from factori.ledger import ResearchLedger
from factori.proof_fake import run_fake_proof_validation
from factori.reports import render_stage_c_verification_report
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    Candidate,
    ControllerActionType,
    DataRequirement,
    FakeExperimentResult,
    FakeProofResult,
    StageCVerificationRecord,
    VerificationLabel,
    VerificationState,
)


class StageCError(RuntimeError):
    """Raised when Stage C verification prerequisites are missing."""


@dataclass(frozen=True)
class StageCResult:
    """Result of deterministic fake Stage C verification."""

    run_id: str
    stage_c_ready_candidates: list[Candidate]
    verified_candidates: list[Candidate]
    verification_records: dict[str, StageCVerificationRecord]
    proof_results: dict[str, FakeProofResult]
    experiment_results: dict[str, FakeExperimentResult]
    artifacts: dict[str, list[ArtifactRef]]
    report_artifact: ArtifactRef
    report_commit_hash: str


def load_stage_c_ready_candidates(run_id: str, ledger: ResearchLedger) -> list[Candidate]:
    """Load candidates selected by the latest Stage C selection budget commit."""
    commits = ledger.list_commits(run_id)
    selection_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.STAGE_C_BUDGET_SELECTED
        ),
        None,
    )
    if selection_commit is None:
        raise StageCError("Stage C-ready candidates not found; run factori select-stage-c first")

    selected_ids = list(selection_commit.payload.get("selected_candidate_ids", []))
    if not selected_ids:
        raise StageCError("No Stage C-ready candidates found; run factori select-stage-c first")

    candidates: dict[str, Candidate] = {}
    for commit in commits:
        if commit.action_type != ControllerActionType.STAGE_B_CHILD_GENERATED:
            continue
        payload = commit.payload.get("candidate")
        if payload is None:
            continue
        candidate = Candidate.model_validate(payload)
        candidates[candidate.id] = candidate

    missing = [candidate_id for candidate_id in selected_ids if candidate_id not in candidates]
    if missing:
        raise StageCError(f"Stage C-ready candidate payloads missing: {', '.join(missing)}")

    return [
        candidates[candidate_id].model_copy(update={"status": BranchStatus.STAGE_C_READY})
        for candidate_id in selected_ids
    ]


def classify_stage_c_branch(candidate: Candidate) -> BranchVerificationType:
    """Classify a Stage C branch into a deterministic fake verification route."""
    text = _candidate_text(candidate)
    if any(token in text for token in ["theorem", "conjecture", "proof", "lemma"]):
        return BranchVerificationType.MATHEMATICAL
    if candidate.data_requirement == DataRequirement.SYNTHETIC_ONLY:
        return BranchVerificationType.SYNTHETIC_EMPIRICAL
    if candidate.data_requirement == DataRequirement.NO_DATA:
        return BranchVerificationType.NO_DATA_METHODOLOGICAL
    return BranchVerificationType.UNSUPPORTED


def run_stage_c(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> StageCResult:
    """Run deterministic fake Stage C verification for selected candidates."""
    store.init_run(run_id)
    stage_c_ready = load_stage_c_ready_candidates(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_STARTED,
        payload={"stage_c_ready_candidate_ids": [candidate.id for candidate in stage_c_ready]},
    )

    verified_candidates: list[Candidate] = []
    verification_records: dict[str, StageCVerificationRecord] = {}
    proof_results: dict[str, FakeProofResult] = {}
    experiment_results: dict[str, FakeExperimentResult] = {}
    artifacts: dict[str, list[ArtifactRef]] = {}

    for candidate in stage_c_ready:
        artifacts[candidate.id] = []
        branch_type = classify_stage_c_branch(candidate)
        record: StageCVerificationRecord

        if branch_type == BranchVerificationType.MATHEMATICAL:
            proof_result = run_fake_proof_validation(candidate)
            proof_results[candidate.id] = proof_result
            proof_artifact = _write_fake_proof_artifact(run_id, proof_result, store, ledger)
            artifacts[candidate.id].append(proof_artifact)
            record = _record_from_proof(candidate, branch_type, proof_result, [proof_artifact])
        elif branch_type == BranchVerificationType.SYNTHETIC_EMPIRICAL:
            experiment_result = run_fake_synthetic_experiment(candidate)
            experiment_results[candidate.id] = experiment_result
            experiment_artifact = _write_fake_experiment_artifact(
                run_id,
                experiment_result,
                store,
                ledger,
            )
            artifacts[candidate.id].append(experiment_artifact)
            record = _record_from_experiment(
                candidate,
                branch_type,
                experiment_result,
                [experiment_artifact],
            )
        elif branch_type == BranchVerificationType.NO_DATA_METHODOLOGICAL:
            record = run_fake_no_data_methodological_validation(candidate)
            _commit_no_data_validation(run_id, record, ledger)
        else:
            record = StageCVerificationRecord(
                candidate_id=candidate.id,
                branch_type=branch_type,
                label=VerificationLabel.UNSUPPORTED,
                status=BranchStatus.STOP_FAILURE,
                reason="branch type is unsupported by the deterministic MVP verifier",
            )

        verification_artifact = _write_verification_decision_artifact(
            run_id,
            record,
            store,
            ledger,
        )
        artifacts[candidate.id].append(verification_artifact)
        verification_records[candidate.id] = record
        verified_candidates.append(_candidate_with_verification(candidate, record))

    report_artifact, report_commit_hash = _write_stage_c_report(
        run_id=run_id,
        stage_c_ready=stage_c_ready,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
        store=store,
        ledger=ledger,
    )

    return StageCResult(
        run_id=run_id,
        stage_c_ready_candidates=stage_c_ready,
        verified_candidates=verified_candidates,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
        artifacts=artifacts,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
    )


def run_fake_no_data_methodological_validation(candidate: Candidate) -> StageCVerificationRecord:
    """Assign a conservative fake label to non-mathematical NoData branches."""
    text = _candidate_text(candidate)
    if "unsupported" in text or "vague" in text:
        label = VerificationLabel.UNSUPPORTED
        status = BranchStatus.STOP_FAILURE
        reason = "fake no-data methodological branch is too vague to support"
    elif "limitation" in text:
        label = VerificationLabel.LIMITATION
        status = BranchStatus.ACTIVE
        reason = "fake no-data methodological branch is retained only as a limitation"
    else:
        label = VerificationLabel.CONJECTURE
        status = BranchStatus.ACTIVE
        reason = "fake no-data methodological branch has no proof or experiment evidence"
    return StageCVerificationRecord(
        candidate_id=candidate.id,
        branch_type=BranchVerificationType.NO_DATA_METHODOLOGICAL,
        label=label,
        status=status,
        reason=reason,
    )


def _record_from_proof(
    candidate: Candidate,
    branch_type: BranchVerificationType,
    proof_result: FakeProofResult,
    evidence_artifacts: list[ArtifactRef],
) -> StageCVerificationRecord:
    label = (
        proof_result.label
        if claim_label_allowed(proof_result.label, evidence_artifacts)
        else VerificationLabel.UNSUPPORTED
    )
    return StageCVerificationRecord(
        candidate_id=candidate.id,
        branch_type=branch_type,
        label=label,
        status=_status_for_label(label),
        evidence_artifacts=evidence_artifacts,
        proof_result=proof_result,
        reason=proof_result.reason if label == proof_result.label else "proof evidence missing",
    )


def _record_from_experiment(
    candidate: Candidate,
    branch_type: BranchVerificationType,
    experiment_result: FakeExperimentResult,
    evidence_artifacts: list[ArtifactRef],
) -> StageCVerificationRecord:
    label = (
        experiment_result.label
        if claim_label_allowed(experiment_result.label, evidence_artifacts)
        else VerificationLabel.UNSUPPORTED
    )
    return StageCVerificationRecord(
        candidate_id=candidate.id,
        branch_type=branch_type,
        label=label,
        status=_status_for_label(label),
        evidence_artifacts=evidence_artifacts,
        experiment_result=experiment_result,
        reason=experiment_result.reason
        if label == experiment_result.label
        else "experiment evidence missing",
    )


def _write_fake_proof_artifact(
    run_id: str,
    proof_result: FakeProofResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"fake-proof-{proof_result.candidate_id}",
        artifact_type=ArtifactType.LEAN,
        data=proof_result,
        metadata={
            "stage": "stage_c",
            "fake": True,
            "evidence_role": PROOF_EVIDENCE_ROLE,
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=proof_result.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_PROOF_VALIDATED,
        payload=proof_result.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_fake_experiment_artifact(
    run_id: str,
    experiment_result: FakeExperimentResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"fake-synthetic-experiment-{experiment_result.candidate_id}",
        artifact_type=ArtifactType.EXPERIMENT,
        data=experiment_result,
        metadata={
            "stage": "stage_c",
            "fake": True,
            "evidence_role": SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=experiment_result.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_SYNTHETIC_EXPERIMENT_RUN,
        payload=experiment_result.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _commit_no_data_validation(
    run_id: str,
    record: StageCVerificationRecord,
    ledger: ResearchLedger,
) -> None:
    ledger.append_commit(
        run_id=run_id,
        candidate_id=record.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_NO_DATA_VALIDATED,
        payload=record.model_dump(mode="json"),
    )


def _write_verification_decision_artifact(
    run_id: str,
    record: StageCVerificationRecord,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"stage-c-verification-{record.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=record,
        metadata={"stage": "stage_c", "fake": True, "report": "verification_decision"},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=record.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_DECIDED,
        payload=record.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_stage_c_report(
    *,
    run_id: str,
    stage_c_ready: list[Candidate],
    verification_records: dict[str, StageCVerificationRecord],
    proof_results: dict[str, FakeProofResult],
    experiment_results: dict[str, FakeExperimentResult],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, str]:
    markdown = render_stage_c_verification_report(
        run_id=run_id,
        stage_c_ready=stage_c_ready,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="stage-c-verification-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "stage_c", "fake": True},
    )
    label_counts = _label_counts(verification_records.values())
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_REPORT_WRITTEN,
        payload={
            "stage_c_ready": len(stage_c_ready),
            "fake_proof_runs": len(proof_results),
            "fake_synthetic_experiments": len(experiment_results),
            **label_counts,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash


def _candidate_with_verification(
    candidate: Candidate,
    record: StageCVerificationRecord,
) -> Candidate:
    return candidate.model_copy(
        update={
            "verification": VerificationState(
                labels=[record.label],
                evidence_artifacts=record.evidence_artifacts,
                notes=record.reason,
            ),
            "status": record.status,
        }
    )


def _status_for_label(label: VerificationLabel) -> BranchStatus:
    if label in {
        VerificationLabel.LEAN_VERIFIED,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
    }:
        return BranchStatus.STOP_SUCCESS
    if label in {VerificationLabel.NEGATIVE_RESULT, VerificationLabel.UNSUPPORTED}:
        return BranchStatus.STOP_FAILURE
    return BranchStatus.ACTIVE


def _label_counts(records) -> dict[str, int]:
    labels = [record.label for record in records]
    return {
        "lean_verified": labels.count(VerificationLabel.LEAN_VERIFIED),
        "synthetic_experiment_verified": labels.count(
            VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        ),
        "negative_results": labels.count(VerificationLabel.NEGATIVE_RESULT),
        "conjectures": labels.count(VerificationLabel.CONJECTURE),
        "limitations": labels.count(VerificationLabel.LIMITATION),
        "unsupported": labels.count(VerificationLabel.UNSUPPORTED),
    }


def _candidate_text(candidate: Candidate) -> str:
    parts = [
        candidate.id,
        candidate.method or "",
        candidate.question,
        candidate.hypothesis or "",
        candidate.theory or "",
        candidate.experiment or "",
        candidate.variant_type or "",
        " ".join(str(value) for value in candidate.symbolic_state.values()),
    ]
    return " ".join(parts).lower()
