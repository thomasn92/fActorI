"""Deterministic intake for structured reviewer change requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.claim_evidence import latest_claim_evidence_map_path
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimEvidenceMap,
    ControllerActionType,
    HumanReviewArtifact,
    ReviewerChangeRequest,
    ReviewerChangeRequestSet,
)


class ReviewerChangeRequestError(RuntimeError):
    """Raised when structured reviewer requests fail validation."""


@dataclass(frozen=True)
class ReviewerChangeRequestIngestResult:
    """Persisted immutable reviewer request set."""

    run_id: str
    request_set: ReviewerChangeRequestSet
    request_set_number: int
    persistence: PersistenceResult
    request_set_artifact: ArtifactRef


def load_reviewer_change_request_set(
    request_file: str | Path,
) -> ReviewerChangeRequestSet:
    """Load one local structured reviewer request set."""
    path = Path(request_file)
    try:
        return ReviewerChangeRequestSet.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise ReviewerChangeRequestError(
            f"Unable to read reviewer change request file: {path}"
        ) from exc
    except ValidationError as exc:
        raise ReviewerChangeRequestError(
            f"Invalid reviewer change request set: {exc}"
        ) from exc


def ingest_reviewer_change_requests(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    request_file: str | Path,
) -> ReviewerChangeRequestIngestResult:
    """Validate and persist one immutable structured reviewer request set."""
    root_path = Path(root)
    request_set = load_reviewer_change_request_set(request_file)
    existing = load_reviewer_change_request_sets(run_id=run_id, root=root_path)
    if any(item.request_set_id == request_set.request_set_id for item in existing):
        raise ReviewerChangeRequestError(
            f"request set already exists: {request_set.request_set_id}"
        )
    _validate_request_set(run_id=run_id, root=root_path, request_set=request_set)
    number = len(existing) + 1
    artifact_id = f"reviewer-change-request-set-{number:04d}"
    metadata = {
        "stage": "reviewer_change_request_intake",
        "artifact_role": "reviewer_change_request_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id,
                ArtifactType.REPORT,
                request_set,
                "json",
                metadata,
            )
        ],
        action_type=ControllerActionType.REVIEWER_CHANGE_REQUESTS_INGESTED,
        commit_payload={
            "run_id": run_id,
            "request_set_id": request_set.request_set_id,
            "request_set_number": number,
            "request_count": len(request_set.requests),
            "publication_ready": False,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )
    return ReviewerChangeRequestIngestResult(
        run_id=run_id,
        request_set=request_set,
        request_set_number=number,
        persistence=persistence,
        request_set_artifact=persistence.artifacts[0],
    )


def inspect_reviewer_change_requests(
    *, run_id: str, root: str | Path = "."
) -> dict[str, object]:
    """Inspect immutable structured reviewer request sets without mutation."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    if not run_path.is_dir():
        raise ReviewerChangeRequestError(f"No run directory found for run_id={run_id}.")
    request_sets = load_reviewer_change_request_sets(run_id=run_id, root=root_path)
    if not request_sets:
        raise ReviewerChangeRequestError(
            f"No reviewer change requests found for run_id={run_id}."
        )
    paths = reviewer_change_request_paths(root_path, run_id)
    return {
        "run_id": run_id,
        "reviewer_change_requests_present": True,
        "reviewer_request_set_count": len(request_sets),
        "latest_request_set_id": request_sets[-1].request_set_id,
        "request_count": sum(len(item.requests) for item in request_sets),
        "request_sets": [item.model_dump(mode="json") for item in request_sets],
        "request_set_paths": [path.relative_to(root_path).as_posix() for path in paths],
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }


def load_reviewer_change_request_sets(
    *, run_id: str, root: Path
) -> list[ReviewerChangeRequestSet]:
    """Load persisted request sets in immutable intake order."""
    result: list[ReviewerChangeRequestSet] = []
    for path in reviewer_change_request_paths(root, run_id):
        try:
            result.append(
                ReviewerChangeRequestSet.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError) as exc:
            raise ReviewerChangeRequestError(
                f"Invalid persisted reviewer request set: {path}"
            ) from exc
    return result


def reviewer_change_request_paths(root: Path, run_id: str) -> list[Path]:
    """Return immutable request-set artifact paths in numeric order."""
    reports = root / "runs" / run_id / "reports"
    return sorted(
        path
        for path in reports.glob("reviewer-change-request-set-*.json")
        if not path.name.endswith(".meta.json")
    )


def accepted_citation_key(request: ReviewerChangeRequest) -> str | None:
    """Normalize a citation key carried by a structured citation request."""
    text = (request.requested_text_optional or "").strip()
    match = re.fullmatch(r"\[@([^\]]+)\]", text)
    return match.group(1) if match else text or None


def _validate_request_set(
    *, run_id: str, root: Path, request_set: ReviewerChangeRequestSet
) -> None:
    if request_set.run_id != run_id:
        raise ReviewerChangeRequestError("reviewer request run_id does not match")
    if not request_set.reviewer_attestation.strip():
        raise ReviewerChangeRequestError("reviewer request attestation is required")
    if (
        request_set.creates_scientific_validation
        or request_set.implies_publication_readiness
        or request_set.is_verification_evidence
    ):
        raise ReviewerChangeRequestError(
            "reviewer requests cannot create validation, readiness, or evidence"
        )
    reports = root / "runs" / run_id / "reports"
    review = _read_model(reports / "human-review-artifact.json", HumanReviewArtifact)
    if review is None or review.review_id != request_set.review_id:
        raise ReviewerChangeRequestError(
            "reviewer request review_id does not match the human-review artifact"
        )
    target = _resolve_run_path(root, run_id, request_set.target_artifact_path)
    preferred = _preferred_manuscript_path(reports).resolve()
    if target.resolve() != preferred:
        raise ReviewerChangeRequestError(
            "target_artifact_path must be the current preferred manuscript"
        )
    markdown = target.read_text(encoding="utf-8")
    headings = {
        match.group(1).strip().casefold()
        for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", markdown, re.MULTILINE)
    }
    claim_map_path = latest_claim_evidence_map_path(root, run_id)
    claim_map = (
        ClaimEvidenceMap.model_validate_json(claim_map_path.read_text(encoding="utf-8"))
        if claim_map_path is not None
        else None
    )
    registry = _read_model(reports / "citation-registry.json", CitationRegistry)
    ids = [request.request_id for request in request_set.requests]
    if len(ids) != len(set(ids)):
        raise ReviewerChangeRequestError("reviewer request IDs must be unique")
    for request in request_set.requests:
        _validate_request(
            request=request,
            headings=headings,
            claim_map=claim_map,
            registry=registry,
        )


def _validate_request(
    *,
    request: ReviewerChangeRequest,
    headings: set[str],
    claim_map: ClaimEvidenceMap | None,
    registry: CitationRegistry | None,
) -> None:
    if request.target_type == "section" and not request.target_section_optional:
        raise ReviewerChangeRequestError("section target requires a target section")
    if request.target_section_optional and (
        request.target_section_optional.casefold() not in headings
    ):
        raise ReviewerChangeRequestError(
            f"unknown target section: {request.target_section_optional}"
        )
    links = claim_map.links if claim_map is not None else []
    matching_links = [
        link
        for link in links
        if (
            request.target_claim_id_optional is None
            or link.claim_id == request.target_claim_id_optional
        )
        and (
            request.target_claim_text_hash_optional is None
            or link.claim_text_hash == request.target_claim_text_hash_optional
        )
    ]
    if (
        request.target_claim_id_optional or request.target_claim_text_hash_optional
    ) and not matching_links:
        raise ReviewerChangeRequestError("unknown claim ID or claim text hash")
    if request.target_type == "claim" and not matching_links:
        raise ReviewerChangeRequestError("claim target requires a known claim ID or hash")
    evidence_id = request.target_evidence_artifact_id_optional
    if evidence_id and not any(
        evidence_id
        in {
            *link.supporting_proof_artifact_ids,
            *link.supporting_experiment_artifact_ids,
        }
        for link in links
    ):
        raise ReviewerChangeRequestError(f"unknown evidence artifact ID: {evidence_id}")
    if request.requested_action == "add_existing_citation":
        key = accepted_citation_key(request)
        accepted = (
            {
                record.citation_key
                for record in registry.citations
                if record.accepted_for_registry and record.source_status != "rejected"
            }
            if registry is not None
            else set()
        )
        if key not in accepted:
            raise ReviewerChangeRequestError(
                "citation request must name an accepted registry citation key"
            )
    if request.requested_action in {
        "add_existing_proof_reference",
        "add_existing_experiment_reference",
    }:
        if not evidence_id or not matching_links:
            raise ReviewerChangeRequestError(
                "existing evidence reference requires scoped claim and artifact targets"
            )
        field = (
            "supporting_proof_artifact_ids"
            if request.requested_action == "add_existing_proof_reference"
            else "supporting_experiment_artifact_ids"
        )
        if not any(evidence_id in getattr(link, field) for link in matching_links):
            raise ReviewerChangeRequestError(
                "evidence artifact is outside the targeted claim scope"
            )


def _resolve_run_path(root: Path, run_id: str, value: str) -> Path:
    path = Path(value)
    full = path if path.is_absolute() else root / path
    run_root = (root / "runs" / run_id).resolve()
    try:
        full.resolve().relative_to(run_root)
    except ValueError as exc:
        raise ReviewerChangeRequestError("target artifact is outside the run") from exc
    if not full.is_file():
        raise ReviewerChangeRequestError(f"target artifact does not exist: {value}")
    return full


def _preferred_manuscript_path(reports: Path) -> Path:
    cycle_paths = sorted(reports.glob("reconciled-manuscript-cycle-*.md"))
    for path in reversed(cycle_paths):
        if path.is_file():
            return path
    for name in (
        "reconciled-manuscript-draft.md",
        "evidence-aware-refreshed-manuscript-draft.md",
        "revised-manuscript-draft.md",
        "complete-manuscript-draft.md",
    ):
        path = reports / name
        if path.is_file():
            return path
    raise ReviewerChangeRequestError("No preferred manuscript was found.")


def _read_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


__all__ = [
    "ReviewerChangeRequestError",
    "ReviewerChangeRequestIngestResult",
    "accepted_citation_key",
    "ingest_reviewer_change_requests",
    "inspect_reviewer_change_requests",
    "load_reviewer_change_request_set",
    "load_reviewer_change_request_sets",
    "reviewer_change_request_paths",
]
