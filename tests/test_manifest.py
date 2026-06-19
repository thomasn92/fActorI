from __future__ import annotations

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest, build_reproducibility_manifest
from factori.manuscript_plan import run_manuscript_planning
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactType,
    ConstraintSet,
    ControllerActionType,
    LedgerSummary,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_artifact_manifest_is_deterministic(tmp_path) -> None:
    store, _ = _run_pipeline_to_draft(tmp_path)

    first = build_artifact_manifest("run-1", store)
    second = build_artifact_manifest("run-1", store)

    assert first == second
    assert first.artifacts


def test_all_artifacts_in_manifest_have_hashes(tmp_path) -> None:
    store, _ = _run_pipeline_to_draft(tmp_path)

    manifest = build_artifact_manifest("run-1", store)

    assert all(entry.content_hash for entry in manifest.artifacts)


def test_evidence_artifacts_require_producing_commits(tmp_path) -> None:
    store, _ = _run_pipeline_to_draft(tmp_path)

    manifest = build_artifact_manifest("run-1", store)
    evidence_entries = [entry for entry in manifest.artifacts if entry.is_evidence]

    assert evidence_entries
    assert all(entry.producing_commit_hash for entry in evidence_entries)


def test_latex_artifacts_are_never_verification_evidence(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)
    artifact = store.write_markdown(
        run_id="run-1",
        artifact_id="presentation-paper",
        artifact_type=ArtifactType.LATEX,
        markdown="\\section{Presentation only}",
        metadata={"evidence_role": "fake_proof"},
    )
    commit = ledger.append_commit(
        run_id="run-1",
        parent_hash=ledger.latest_commit_hash("run-1"),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact.id},
        artifact_refs=[artifact],
    )
    linked = store.link_artifact_to_commit(artifact, commit.commit_hash)

    manifest = build_artifact_manifest("run-1", store)
    entry = next(item for item in manifest.artifacts if item.path == linked.path)

    assert not entry.is_evidence
    assert entry.is_presentation


def test_reproducibility_manifest_detects_missing_claim_table() -> None:
    manifest = ArtifactManifest(
        run_id="run-1",
        artifacts=[
            _entry("runs/run-1/reports/draft-skeleton.json"),
            _entry("runs/run-1/reports/manuscript-plan.json"),
            _entry("runs/run-1/reports/final-nucleus.json"),
            _entry("runs/run-1/reports/blocked-claims.json"),
        ],
        evidence_artifact_count=0,
        presentation_artifact_count=4,
    )

    reproducibility = build_reproducibility_manifest(
        "run-1",
        manifest,
        _ledger_summary(),
    )

    assert not reproducibility.reproducible
    assert "claim_table_exists" in reproducibility.blocking_issues


def test_reproducibility_manifest_detects_missing_artifact_hash() -> None:
    manifest = ArtifactManifest(
        run_id="run-1",
        artifacts=[
            _entry("runs/run-1/reports/claim-table.json", content_hash=None),
            _entry("runs/run-1/reports/draft-skeleton.json"),
            _entry("runs/run-1/reports/manuscript-plan.json"),
            _entry("runs/run-1/reports/final-nucleus.json"),
            _entry("runs/run-1/reports/blocked-claims.json"),
        ],
        evidence_artifact_count=0,
        presentation_artifact_count=5,
    )

    reproducibility = build_reproducibility_manifest(
        "run-1",
        manifest,
        _ledger_summary(),
    )

    assert not reproducibility.reproducible
    assert "all_artifacts_have_hashes" in reproducibility.blocking_issues


def test_reproducibility_manifest_detects_evidence_without_producing_commit() -> None:
    manifest = ArtifactManifest(
        run_id="run-1",
        artifacts=[
            _entry("runs/run-1/reports/claim-table.json"),
            _entry("runs/run-1/reports/draft-skeleton.json"),
            _entry("runs/run-1/reports/manuscript-plan.json"),
            _entry("runs/run-1/reports/final-nucleus.json"),
            _entry("runs/run-1/reports/blocked-claims.json"),
            _entry(
                "runs/run-1/lean/fake-proof-candidate.json",
                artifact_type=ArtifactType.LEAN,
                is_evidence=True,
                producing_commit_hash=None,
            ),
        ],
        evidence_artifact_count=1,
        presentation_artifact_count=5,
    )

    reproducibility = build_reproducibility_manifest(
        "run-1",
        manifest,
        _ledger_summary(),
    )

    assert not reproducibility.reproducible
    assert (
        "all_evidence_artifacts_have_producing_commits"
        in reproducibility.blocking_issues
    )


def test_reproducibility_manifest_passes_for_complete_deterministic_run(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)
    manifest = build_artifact_manifest("run-1", store)
    summary = LedgerSummary(
        run_id="run-1",
        commit_count=len(ledger.list_commits("run-1")),
        root_commit_hash=ledger.list_commits("run-1")[0].commit_hash,
        latest_commit_hash=ledger.latest_commit_hash("run-1"),
        action_type_counts={},
        candidate_count=1,
        artifact_count=len(manifest.artifacts),
        verification_decision_count=1,
        human_tail_escalation_count=0,
    )

    reproducibility = build_reproducibility_manifest("run-1", manifest, summary)

    assert reproducibility.reproducible
    assert reproducibility.blocking_issues == []


def _run_pipeline_to_draft(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _entry(
    path: str,
    *,
    artifact_type: ArtifactType = ArtifactType.REPORT,
    content_hash: str | None = "0" * 64,
    producing_commit_hash: str | None = "1" * 64,
    is_evidence: bool = False,
) -> ArtifactManifestEntry:
    return ArtifactManifestEntry(
        artifact_id=path.rsplit("/", maxsplit=1)[-1].split(".")[0],
        artifact_type=artifact_type,
        path=path,
        content_hash=content_hash,
        producing_commit_hash=producing_commit_hash,
        is_evidence=is_evidence,
        is_presentation=not is_evidence,
    )


def _ledger_summary() -> LedgerSummary:
    return LedgerSummary(
        run_id="run-1",
        commit_count=1,
        root_commit_hash="2" * 64,
        latest_commit_hash="3" * 64,
        action_type_counts={},
        candidate_count=1,
        artifact_count=1,
        verification_decision_count=0,
        human_tail_escalation_count=0,
    )
