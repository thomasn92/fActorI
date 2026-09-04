"""Local filesystem artifact store."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from factori.config import FACTORI_KERNEL_BINARY_ENV, RUN_SUBDIRECTORIES, RUNS_DIR
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

    def __init__(
        self,
        root: str | Path = ".",
        *,
        kernel_binary: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        configured_kernel = (
            kernel_binary
            if kernel_binary is not None
            else os.environ.get(FACTORI_KERNEL_BINARY_ENV)
        )
        self.kernel_binary = (
            None if configured_kernel is None else Path(configured_kernel).expanduser().resolve()
        )

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
        filename_stem: str | None = None,
    ) -> ArtifactRef:
        """Write a canonical JSON artifact and return its reference."""
        self.init_run(run_id)
        path = self._artifact_path(
            run_id,
            artifact_type,
            artifact_id,
            "json",
            filename_stem=filename_stem,
        )
        self._atomic_write_text(path, canonical_json(data) + "\n")
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
        filename_stem: str | None = None,
    ) -> ArtifactRef:
        """Write a Markdown artifact and return its reference."""
        self.init_run(run_id)
        path = self._artifact_path(
            run_id,
            artifact_type,
            artifact_id,
            "md",
            filename_stem=filename_stem,
        )
        self._atomic_write_text(path, markdown)
        return self._ref(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            metadata={"format": "markdown", **(metadata or {})},
            producing_commit_hash=producing_commit_hash,
        )

    def write_text(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        text: str,
        extension: str,
        format_label: str,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
        filename_stem: str | None = None,
    ) -> ArtifactRef:
        """Write a generic text artifact and return its reference."""
        if not extension or "/" in extension or "\\" in extension:
            raise ArtifactError("text artifact extension must be a simple suffix")
        self.init_run(run_id)
        path = self._artifact_path(
            run_id,
            artifact_type,
            artifact_id,
            extension.removeprefix("."),
            filename_stem=filename_stem,
        )
        self._atomic_write_text(path, text)
        return self._ref(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            metadata={"format": format_label, **(metadata or {})},
            producing_commit_hash=producing_commit_hash,
        )

    def write_bytes(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        content: bytes,
        extension: str,
        format_label: str,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
        filename_stem: str | None = None,
    ) -> ArtifactRef:
        """Write a generic binary artifact and return its reference."""
        if not extension or "/" in extension or "\\" in extension:
            raise ArtifactError("binary artifact extension must be a simple suffix")
        self.init_run(run_id)
        path = self._artifact_path(
            run_id,
            artifact_type,
            artifact_id,
            extension.removeprefix("."),
            filename_stem=filename_stem,
        )
        self._atomic_write_bytes(path, content)
        return self._ref(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            metadata={"format": format_label, **(metadata or {})},
            producing_commit_hash=producing_commit_hash,
        )

    def link_artifact_to_commit(self, artifact: ArtifactRef, commit_hash: str) -> ArtifactRef:
        """Return and persist an artifact reference linked to its producing commit."""
        linked = artifact.model_copy(update={"producing_commit_hash": commit_hash})
        meta_path = self.root / f"{linked.path}.meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(meta_path, canonical_json(linked) + "\n")
        return linked

    def _atomic_write_text(self, path: Path, text: str) -> None:
        """Atomically replace one UTF-8 text file using stable LF newlines."""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self._atomic_write_bytes(path, normalized.encode("utf-8"))

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        """Write and fsync a same-directory temp file before atomic replacement."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        handle_open = False
        try:
            handle = os.fdopen(descriptor, "wb")
            handle_open = True
            descriptor = -1
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            handle_open = False
            os.replace(temp_path, path)
            self._fsync_directory(path.parent)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            if handle_open:
                handle.close()
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Best-effort directory fsync after replace on supporting platforms."""
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _artifact_path(
        self,
        run_id: str,
        artifact_type: ArtifactType,
        artifact_id: str,
        extension: str,
        filename_stem: str | None = None,
    ) -> Path:
        directory = ARTIFACT_DIRECTORY_BY_TYPE[artifact_type]
        safe_id = (filename_stem or artifact_id).replace("/", "_")
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
