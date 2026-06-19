from __future__ import annotations

import pytest
from pydantic import ValidationError

from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Candidate,
    DataRequirement,
    ScoreVector,
    VerificationState,
)

HASH = "a" * 64


def test_schemas_serialize_and_deserialize() -> None:
    candidate = Candidate(
        id="candidate-001",
        domain="human geography",
        question="Can a deterministic ledger preserve provenance?",
        data_requirement=DataRequirement.NO_DATA,
        symbolic_state={"objects": ["graphs"]},
    )

    restored = Candidate.model_validate_json(candidate.model_dump_json())

    assert restored == candidate
    assert restored.is_mvp_admissible()
    assert ScoreVector(novelty=1.0, feasibility=1.0, verifiability=1.0).base_score() <= 1.0


def test_invalid_data_regime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Candidate(
            id="candidate-001",
            question="Invalid data regime?",
            data_requirement="PrivateDataset",
        )


@pytest.mark.parametrize(
    "data_requirement",
    [DataRequirement.PUBLIC_DOWNLOAD, DataRequirement.USER_PROVIDED],
)
def test_real_data_candidates_are_not_mvp_admissible(data_requirement: DataRequirement) -> None:
    candidate = Candidate(
        id=f"candidate-{data_requirement.value}",
        question="Does this require real data?",
        data_requirement=data_requirement,
    )

    assert not candidate.is_mvp_admissible()


def test_latex_artifacts_are_not_verification_evidence() -> None:
    latex = ArtifactRef(
        id="paper",
        type=ArtifactType.LATEX,
        path="runs/run-1/latex/paper.tex",
        content_hash=HASH,
        producing_commit_hash=HASH,
    )

    assert not latex.is_mvp_verification_evidence()
    with pytest.raises(ValidationError):
        VerificationState(evidence_artifacts=[latex])
