"""Local filesystem artifact store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factori.config import RUN_SUBDIRECTORIES, RUNS_DIR
from factori.hashing import canonical_json, sha256_file
from factori.schemas import ArtifactRef, ArtifactType

ARTIFACT_DIRECTORY_BY_TYPE = {
    ArtifactType.CANDIDATE: "candidates",
    ArtifactType.SCORE: "scores",
    ArtifactType.REPORT: "reports",
    ArtifactType.LITERATURE: "literature",
    ArtifactType.LEAN: "lean",
    ArtifactType.EXPERIMENT: "experiments",
    ArtifactType.LOG: "logs",
    ArtifactType.LATEX: "latex",
}


class ArtifactError(RuntimeError):
    """Raised when an artifact invariant is violated."""


class ArtifactStore:
    """Filesystem store rooted at a local project directory."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root)

    def run_path(self, run_id: str) -> Path:
        """Return the run directory."""
        return self.root / RUNS_DIR / run_id

    def init_run(self, run_id: str) -> Path:
        """Create the required run artifact directory structure."""
        run_path = self.run_path(run_id)
        for directory in RUN_SUBDIRECTORIES:
            (run_path / directory).mkdir(parents=True, exist_ok=True)
        return run_path

    def expected_directories(self, run_id: str) -> list[Path]:
        """Return the required directories for a run."""
        return [self.run_path(run_id) / directory for directory in RUN_SUBDIRECTORIES]

    def validate_run_structure(self, run_id: str) -> None:
        """Raise if the run directory is missing required subdirectories."""
        missing = [path for path in self.expected_directories(run_id) if not path.is_dir()]
        if missing:
            joined = ", ".join(str(path) for path in missing)
            raise ArtifactError(f"missing run directories: {joined}")

    def write_json(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        data: Any,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
    ) -> ArtifactRef:
        """Write a canonical JSON artifact and return its reference."""
        self.init_run(run_id)
        path = self._artifact_path(run_id, artifact_type, artifact_id, "json")
        path.write_text(canonical_json(data) + "\n", encoding="utf-8")
        return self._ref(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            metadata={"format": "json", **(metadata or {})},
            producing_commit_hash=producing_commit_hash,
        )

    def write_markdown(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        markdown: str,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
    ) -> ArtifactRef:
        """Write a Markdown artifact and return its reference."""
        self.init_run(run_id)
        path = self._artifact_path(run_id, artifact_type, artifact_id, "md")
        path.write_text(markdown, encoding="utf-8")
        return self._ref(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            metadata={"format": "markdown", **(metadata or {})},
            producing_commit_hash=producing_commit_hash,
        )

    def link_artifact_to_commit(self, artifact: ArtifactRef, commit_hash: str) -> ArtifactRef:
        """Return and persist an artifact reference linked to its producing commit."""
        linked = artifact.model_copy(update={"producing_commit_hash": commit_hash})
        meta_path = self.root / f"{linked.path}.meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(canonical_json(linked) + "\n", encoding="utf-8")
        return linked

    def _artifact_path(
        self,
        run_id: str,
        artifact_type: ArtifactType,
        artifact_id: str,
        extension: str,
    ) -> Path:
        directory = ARTIFACT_DIRECTORY_BY_TYPE[artifact_type]
        safe_id = artifact_id.replace("/", "_")
        return self.run_path(run_id) / directory / f"{safe_id}.{extension}"

    def _ref(
        self,
        *,
        artifact_id: str,
        artifact_type: ArtifactType,
        path: Path,
        metadata: dict[str, Any],
        producing_commit_hash: str | None,
    ) -> ArtifactRef:
        relative_path = path.relative_to(self.root)
        return ArtifactRef(
            id=artifact_id,
            type=artifact_type,
            path=relative_path.as_posix(),
            content_hash=sha256_file(path),
            producing_commit_hash=producing_commit_hash,
            metadata=metadata,
        )
