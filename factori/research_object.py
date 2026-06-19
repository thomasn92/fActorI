"""Deterministic research object packaging."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest, build_reproducibility_manifest
from factori.reports import render_research_object_markdown
from factori.run_summary import build_branch_outcomes, build_ledger_summary
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    FinalNucleus,
    LedgerCommit,
    PackagedOutput,
    ResearchObject,
    ResearchObjectManifest,
)


class ResearchObjectError(RuntimeError):
    """Raised when research object prerequisites are missing."""


@dataclass(frozen=True)
class _RequiredRefs:
    final_nucleus: FinalNucleus
    final_nucleus_ref: ArtifactRef
    manuscript_plan_ref: ArtifactRef
    draft_skeleton_ref: ArtifactRef
    claim_table_ref: ArtifactRef
    blocked_claims_ref: ArtifactRef
    checklist_ref: ArtifactRef
    stage_reports: dict[str, ArtifactRef]


def build_research_object(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PackagedOutput:
    """Package the deterministic run into a local research object."""
    store.init_run(run_id)
    required = _load_required_refs(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RESEARCH_OBJECT_PACKAGING_STARTED,
        payload={
            "run_id": run_id,
            "final_nucleus_id": required.final_nucleus.id,
            "draft_skeleton": required.draft_skeleton_ref.id,
        },
    )

    artifact_manifest = build_artifact_manifest(run_id, store)
    ledger_summary = build_ledger_summary(run_id, ledger)
    branch_outcomes = build_branch_outcomes(run_id, ledger, store)
    reproducibility_manifest = build_reproducibility_manifest(
        run_id,
        artifact_manifest,
        ledger_summary,
    )

    artifact_manifest_ref = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="artifact-manifest",
        payload=artifact_manifest,
        action_type=ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        metadata={"package_part": "artifact_manifest"},
    )
    ledger_summary_ref = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="ledger-summary",
        payload=ledger_summary,
        action_type=ControllerActionType.LEDGER_SUMMARY_WRITTEN,
        metadata={"package_part": "ledger_summary"},
    )
    branch_outcomes_ref = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="branch-outcomes",
        payload={"branch_outcomes": branch_outcomes},
        action_type=ControllerActionType.BRANCH_OUTCOMES_WRITTEN,
        metadata={"package_part": "branch_outcomes"},
    )
    reproducibility_manifest_ref = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="reproducibility-manifest",
        payload=reproducibility_manifest,
        action_type=ControllerActionType.REPRODUCIBILITY_MANIFEST_WRITTEN,
        metadata={"package_part": "reproducibility_manifest"},
    )

    research_object = ResearchObject(
        run_id=run_id,
        final_nucleus=required.final_nucleus,
        manuscript_plan_ref=required.manuscript_plan_ref,
        draft_skeleton_ref=required.draft_skeleton_ref,
        claim_table_ref=required.claim_table_ref,
        blocked_claims_ref=required.blocked_claims_ref,
        checklist_ref=required.checklist_ref,
        stage_reports=required.stage_reports,
        artifact_manifest_ref=artifact_manifest_ref,
        ledger_summary_ref=ledger_summary_ref,
        branch_outcomes_ref=branch_outcomes_ref,
        reproducibility_manifest_ref=reproducibility_manifest_ref,
        created_at=_latest_timestamp(run_id, ledger),
    )
    research_object_json_ref = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="research-object",
        payload=research_object,
        action_type=ControllerActionType.RESEARCH_OBJECT_WRITTEN,
        metadata={"package_part": "research_object"},
    )
    markdown = render_research_object_markdown(
        research_object=research_object,
        artifact_manifest=artifact_manifest,
        ledger_summary=ledger_summary,
        branch_outcomes=branch_outcomes,
        reproducibility_manifest=reproducibility_manifest,
    )
    research_object_markdown_ref = _write_research_object_markdown(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="research-object",
        markdown=markdown,
        action_type=ControllerActionType.RESEARCH_OBJECT_WRITTEN,
        metadata={"package_part": "research_object_markdown"},
    )

    manifest = ResearchObjectManifest(
        research_object_json=research_object_json_ref,
        research_object_markdown=research_object_markdown_ref,
        artifact_manifest=artifact_manifest_ref,
        ledger_summary=ledger_summary_ref,
        branch_outcomes=branch_outcomes_ref,
        reproducibility_manifest=reproducibility_manifest_ref,
    )
    return PackagedOutput(
        run_id=run_id,
        research_object=research_object,
        manifest=manifest,
        artifact_manifest=artifact_manifest,
        ledger_summary=ledger_summary,
        branch_outcomes=branch_outcomes,
        reproducibility_manifest=reproducibility_manifest,
    )


def _load_required_refs(run_id: str, ledger: ResearchLedger) -> _RequiredRefs:
    commits = ledger.list_commits(run_id)
    draft_commit = _latest_commit(commits, ControllerActionType.DRAFT_SKELETON_BUILT)
    if draft_commit is None:
        raise ResearchObjectError(
            "Draft skeleton artifacts not found; run factori build-draft-skeleton first"
        )

    final_nucleus_commit = _require_commit(
        commits,
        ControllerActionType.FINAL_NUCLEUS_SELECTED,
        "Final nucleus not found; run factori synthesize-abstract first",
    )
    manuscript_plan_commit = _require_commit(
        commits,
        ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        "Manuscript plan not found; run factori plan-manuscript first",
    )
    claim_table_commit = _require_commit(
        commits,
        ControllerActionType.CLAIM_TABLE_BUILT,
        "Claim table not found; run factori plan-manuscript first",
    )
    blocked_claims_commit = _require_commit(
        commits,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        "Blocked claims not found; run factori plan-manuscript first",
    )
    checklist_commit = _require_commit(
        commits,
        ControllerActionType.MANUSCRIPT_CHECKLIST_BUILT,
        "Manuscript checklist not found; run factori build-draft-skeleton first",
    )

    return _RequiredRefs(
        final_nucleus=FinalNucleus.model_validate(final_nucleus_commit.payload),
        final_nucleus_ref=_single_artifact(final_nucleus_commit),
        manuscript_plan_ref=_single_artifact(manuscript_plan_commit),
        draft_skeleton_ref=_single_artifact(draft_commit),
        claim_table_ref=_single_artifact(claim_table_commit),
        blocked_claims_ref=_single_artifact(blocked_claims_commit),
        checklist_ref=_single_artifact(checklist_commit),
        stage_reports=_stage_report_refs(commits),
    )


def _stage_report_refs(commits: list[LedgerCommit]) -> dict[str, ArtifactRef]:
    required_reports = {
        "stage_a": ControllerActionType.STAGE_A_REPORT_WRITTEN,
        "stage_b": ControllerActionType.STAGE_B_REPORT_WRITTEN,
        "stage_c_selection": ControllerActionType.STAGE_C_SELECTION_REPORT_WRITTEN,
        "stage_c_verification": ControllerActionType.STAGE_C_VERIFICATION_REPORT_WRITTEN,
        "abstract_synthesis": ControllerActionType.ABSTRACT_SYNTHESIS_REPORT_WRITTEN,
    }
    reports: dict[str, ArtifactRef] = {}
    for key, action_type in required_reports.items():
        commit = _require_commit(
            commits,
            action_type,
            f"{action_type.value} not found; run the deterministic pipeline first",
        )
        reports[key] = _single_artifact(commit)
    return reports


def _write_research_object_json(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    payload: Any,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "json")
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "json", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload=_payload_for_commit(payload),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_research_object_markdown(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    markdown: str,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "md")
    path.write_text(markdown, encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "markdown", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload={"artifact_id": artifact_id, "format": "markdown", **metadata},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _research_object_path(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str,
    extension: str,
) -> Path:
    directory = store.run_path(run_id) / "research_object"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{artifact_id}.{extension}"


def _artifact_ref(
    store: ArtifactStore,
    artifact_id: str,
    path: Path,
    metadata: dict[str, Any],
) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=path.relative_to(store.root).as_posix(),
        content_hash=sha256_file(path),
        metadata={"stage": "research_object", "fake": True, **metadata},
    )


def _payload_for_commit(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return {"payload": payload}


def _latest_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
) -> LedgerCommit | None:
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    return None


def _require_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    message: str,
) -> LedgerCommit:
    commit = _latest_commit(commits, action_type)
    if commit is None:
        raise ResearchObjectError(message)
    return commit


def _single_artifact(commit: LedgerCommit) -> ArtifactRef:
    if not commit.artifact_refs:
        raise ResearchObjectError(f"{commit.action_type.value} has no artifact reference")
    return commit.artifact_refs[0]


def _latest_timestamp(run_id: str, ledger: ResearchLedger) -> str:
    commits = ledger.list_commits(run_id)
    if not commits:
        return "1970-01-01T00:00:00.000000Z"
    return commits[-1].timestamp
