from __future__ import annotations

import socket
import subprocess

from factori.adapters.fake import (
    FakeExperimentRunner,
    FakeHumanReviewClient,
    FakeLLMClient,
    FakeProofVerifier,
    FakeProseGenerator,
    FakeRetrievalClient,
)
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import (
    Candidate,
    Claim,
    ClaimTable,
    ConstraintSet,
    DataRequirement,
    LiteratureState,
    ManuscriptSectionPlan,
    VerificationLabel,
)
from factori.stage_a import run_stage_a


def test_fake_llm_client_is_deterministic() -> None:
    client = FakeLLMClient()
    constraints = ConstraintSet(domain="human geography", method="optimal transport")

    first = client.generate_candidates("candidate prompt", constraints)
    second = client.generate_candidates("candidate prompt", constraints)

    assert first == second
    assert len(first) == 5
    assert all(candidate.symbolic_state["fake"] is True for candidate in first)
    assert client.review_candidate(first[0], {"novelty": 1}) == client.review_candidate(
        first[0], {"novelty": 1}
    )
    assert client.summarize_context({"b": 2, "a": 1}).startswith(
        "[FAKE CONTEXT SUMMARY]"
    )


def test_stage_a_accepts_fake_llm_without_changing_candidates_or_scores(tmp_path) -> None:
    builtin_store = ArtifactStore(tmp_path / "builtin")
    builtin_ledger = ResearchLedger(
        builtin_store.run_path("run-1") / "ledger.sqlite"
    )
    adapter_store = ArtifactStore(tmp_path / "adapter")
    adapter_ledger = ResearchLedger(
        adapter_store.run_path("run-1") / "ledger.sqlite"
    )
    constraints = ConstraintSet(domain="machine learning", method="calibration")

    builtin = run_stage_a(
        run_id="run-1",
        constraints=constraints,
        store=builtin_store,
        ledger=builtin_ledger,
    )
    adapted = run_stage_a(
        run_id="run-1",
        constraints=constraints,
        store=adapter_store,
        ledger=adapter_ledger,
        llm_client=FakeLLMClient(),
    )

    assert [candidate.id for candidate in builtin.generated_candidates] == [
        candidate.id for candidate in adapted.generated_candidates
    ]
    assert builtin.scores == adapted.scores
    started = adapter_ledger.list_commits("run-1")[0]
    assert started.payload["llm_adapter"]["backend"] == "fake"


def test_fake_retrieval_client_is_deterministic() -> None:
    client = FakeRetrievalClient()

    first = client.search("robust calibration", 4)
    second = client.search("robust calibration", 4)

    assert first == second
    assert all(result.fake for result in first)
    assert client.fetch(first[0].source_id) == client.fetch(first[0].source_id)
    assert client.build_adequacy_certificate("robust calibration", first) == (
        client.build_adequacy_certificate("robust calibration", second)
    )


def test_retrieval_adequacy_accepts_fake_adapter() -> None:
    client = FakeRetrievalClient()
    state = LiteratureState(k=3)

    first = compute_retrieval_adequacy(
        state,
        retrieval_client=client,
        query="distribution shift",
    )
    second = compute_retrieval_adequacy(
        state,
        retrieval_client=client,
        query="distribution shift",
    )

    assert first == second
    assert first.fake


def test_fake_proof_verifier_matches_existing_fake_contract() -> None:
    candidate = Candidate(
        id="candidate-proof",
        question="Can this theorem be checked by the fake validator?",
        theory="Theorem-style proof",
        variant_type="theorem_or_conjecture_form",
    )
    verifier = FakeProofVerifier()

    first = verifier.verify_proof(candidate, {"proof": "placeholder"})
    second = verifier.verify_proof(candidate, {"proof": "placeholder"})

    assert first == second
    assert first.fake
    assert first.proof_attempt_id.startswith("fake-proof-")
    assert "fake" in first.reason.lower()


def test_fake_experiment_runner_is_deterministic_and_labeled_fake() -> None:
    candidate = Candidate(
        id="candidate-synthetic",
        question="Can the fake synthetic contract run?",
        experiment="Deterministic synthetic contract",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    runner = FakeExperimentRunner()

    first = runner.run_synthetic_experiment(candidate, {"seed": 1})
    second = runner.run_synthetic_experiment(candidate, {"seed": 999})

    assert first == second
    assert first.fake
    assert first.generator_name.startswith("deterministic_synthetic")
    assert "fake" in first.reason.lower()


def test_fake_prose_generator_produces_only_placeholder_stub() -> None:
    generator = FakeProseGenerator()
    claim_table = _claim_table()
    section = ManuscriptSectionPlan(
        section_id="theory",
        title="Theory",
        bullets=["Preserve claim labels"],
        allowed_claim_ids=["claim-1"],
    )

    draft = generator.generate_section(section, claim_table)

    assert draft.fake
    assert not draft.polished
    assert not draft.is_verification_evidence
    assert "FAKE SECTION STUB" in draft.content
    assert "polished_prose=false" in draft.content


def test_fake_human_review_never_claims_real_approval() -> None:
    client = FakeHumanReviewClient()

    first = client.request_review({"candidate_id": "candidate-1"})
    second = client.request_review({"candidate_id": "candidate-1"})

    assert first == second
    assert first.fake
    assert not first.approved
    assert not first.reviewer_is_human
    assert first.decision == "NoHumanReviewPerformed"


def test_fake_adapters_do_not_call_network_or_subprocess(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("external operation attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    retrieval = FakeRetrievalClient()
    results = retrieval.search("no external calls", 2)
    retrieval.fetch(results[0].source_id)
    FakeLLMClient().generate_candidates(
        "prompt",
        ConstraintSet(domain="machine learning", method="calibration"),
    )
    FakeProofVerifier().verify_proof(
        Candidate(id="proof", question="Fake theorem proof"),
        {},
    )
    FakeExperimentRunner().run_synthetic_experiment(
        Candidate(
            id="experiment",
            question="Fake synthetic experiment",
            data_requirement=DataRequirement.SYNTHETIC_ONLY,
        ),
        {},
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="nucleus-1",
        claims=[
            Claim(
                claim_id="claim-1",
                claim_text="A deterministic conjectural placeholder.",
                claim_label=VerificationLabel.CONJECTURE,
                candidate_id="candidate-1",
                allowed_in_main_text=False,
                allowed_section="Theory",
                reason="Fake adapter test claim.",
            )
        ],
    )
