"""Artifact references, manifests, and ledger commit schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from factori.schemas.base import HASH_RE, SchemaError, StrictModel
from factori.schemas.enums import ArtifactType, ControllerActionType


class ArtifactRef(StrictModel):
    """Reference to an artifact stored on the local filesystem."""

    id: str = Field(min_length=1)
    type: ArtifactType
    path: str = Field(min_length=1)
    content_hash: str
    producing_commit_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_hash", "producing_commit_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HASH_RE.fullmatch(value):
            raise ValueError("hash values must be lowercase SHA-256 hex digests")
        return value

    def is_mvp_verification_evidence(self) -> bool:
        """Return whether this artifact may serve as verification evidence.

        LaTeX is presentation only. Markdown is also treated as presentation in this MVP,
        even when it is stored under reports.
        """
        suffix = self.path.rsplit(".", maxsplit=1)[-1].lower() if "." in self.path else ""
        if self.metadata.get("is_verification_evidence") is False:
            return False
        if self.type == ArtifactType.LATEX:
            return False
        if suffix in {"md", "markdown", "tex", "pdf"}:
            return False
        return self.type in {
            ArtifactType.CANDIDATE,
            ArtifactType.SCORE,
            ArtifactType.LITERATURE,
            ArtifactType.LEAN,
            ArtifactType.EXPERIMENT,
            ArtifactType.LOG,
            ArtifactType.REPORT,
        }

    def require_evidence_ready(self) -> None:
        """Raise if the artifact cannot be used as verification evidence."""
        if not self.is_mvp_verification_evidence():
            raise SchemaError("presentation artifacts are not verification evidence")
        if self.producing_commit_hash is None:
            raise SchemaError("evidence artifacts require a producing commit hash")


class ArtifactManifestEntry(StrictModel):
    """One artifact entry in the research object manifest."""

    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    path: str = Field(min_length=1)
    content_hash: str | None = None
    producing_commit_hash: str | None = None
    is_evidence: bool
    is_presentation: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactManifest(StrictModel):
    """Derived manifest of run artifacts. The ledger remains authoritative."""

    run_id: str = Field(min_length=1)
    artifacts: list[ArtifactManifestEntry]
    evidence_artifact_count: int = Field(ge=0)
    presentation_artifact_count: int = Field(ge=0)
    source_of_truth: str = "ledger"


class LedgerCommit(StrictModel):
    """Immutable ledger commit."""

    commit_hash: str
    parent_hash: str | None = None
    run_id: str = Field(min_length=1)
    candidate_id: str | None = None
    action_type: ControllerActionType
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    timestamp: str = Field(min_length=1)

    @field_validator("commit_hash", "parent_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HASH_RE.fullmatch(value):
            raise ValueError("hash values must be lowercase SHA-256 hex digests")
        return value

__all__ = [
    "ArtifactRef",
    "ArtifactManifestEntry",
    "ArtifactManifest",
    "LedgerCommit",
]
