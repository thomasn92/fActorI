"""Gated LaTeX render/check scaffold with injected-runner support."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factori.hashing import sha256_bytes, sha256_text
from factori.schemas import LatexCompileCheckReport, LatexRenderConfig, LatexRenderResult


class LatexRenderError(RuntimeError):
    """Raised when LaTeX render/check gates or execution fail before producing a result."""


@dataclass(frozen=True)
class LatexRunnerOutput:
    """Normalized output from an injected or subprocess LaTeX runner."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    pdf_bytes: bytes | None = None
    tool_version: str | None = None


LatexRunner = Callable[[str, LatexRenderConfig], LatexRunnerOutput]
_OVERFULL_BOX_RE = re.compile(
    r"Overfull \\[hv]box \((?P<points>\d+(?:\.\d+)?)pt too (?:wide|high)\)"
)
_UNRESOLVED_REFERENCE_RE = re.compile(
    r"(?:Citation|Reference) `[^']+' .* undefined|There were undefined references",
    re.IGNORECASE,
)
_MAX_ACCEPTABLE_OVERFULL_POINTS = 2.0


@dataclass(frozen=True)
class LatexRenderedDocument:
    """Render metadata together with the PDF bytes needed for persistence."""

    result: LatexRenderResult
    pdf_bytes: bytes | None = None


class LatexRenderer:
    """Optional local LaTeX renderer; disabled unless explicitly gated."""

    def __init__(self, runner: LatexRunner | None = None) -> None:
        self.runner = runner

    def render(self, paper_tex: str, config: LatexRenderConfig) -> LatexRenderResult:
        """Run a gated render/check and return deterministic hashes."""
        return self.render_document(paper_tex, config).result

    def render_document(
        self, paper_tex: str, config: LatexRenderConfig
    ) -> LatexRenderedDocument:
        """Run a gated render/check and retain PDF bytes for an artifact stage."""
        if not config.render_check_enabled:
            return LatexRenderedDocument(
                result=LatexRenderResult(
                    run_id=config.run_id,
                    backend=config.backend,
                    tool_name=config.latex_executable or "not_configured",
                    exit_code=0,
                    stdout_hash=sha256_text(""),
                    stderr_hash=sha256_text(""),
                    tex_hash=sha256_text(paper_tex),
                    passed=True,
                    warnings=["LaTeX render check was not requested."],
                    reason="Render check skipped.",
                )
            )
        if not config.allow_external_tools:
            raise LatexRenderError(
                "External render tools are disabled. Set allow_external_tools=true "
                "to use LaTeX render checks."
            )
        if not config.latex_executable:
            raise LatexRenderError(
                "LaTeX render requested but executable is not configured or not found."
            )
        if self.runner is None and shutil.which(config.latex_executable) is None:
            raise LatexRenderError(
                "LaTeX render requested but executable is not configured or not found."
            )

        output = (
            self.runner(paper_tex, config)
            if self.runner is not None
            else _subprocess_latex_runner(paper_tex, config)
        )
        overfull_points = [
            float(match.group("points"))
            for match in _OVERFULL_BOX_RE.finditer(output.stdout + "\n" + output.stderr)
            if float(match.group("points")) > _MAX_ACCEPTABLE_OVERFULL_POINTS
        ]
        unresolved_references = bool(
            _UNRESOLVED_REFERENCE_RE.search(output.stdout + "\n" + output.stderr)
        )
        layout_passed = not overfull_points
        passed = output.exit_code == 0 and layout_passed and not unresolved_references
        warnings: list[str] = []
        if output.exit_code != 0:
            warnings.append("LaTeX render check failed.")
        if overfull_points:
            warnings.append(
                "LaTeX layout check failed: "
                f"{len(overfull_points)} overfull boxes exceed "
                f"{_MAX_ACCEPTABLE_OVERFULL_POINTS:g}pt; "
                f"maximum={max(overfull_points):.2f}pt."
            )
        if unresolved_references:
            warnings.append("LaTeX reference check failed: unresolved citations or references.")
        return LatexRenderedDocument(
            result=LatexRenderResult(
                run_id=config.run_id,
                backend=config.backend,
                tool_name=config.latex_executable,
                tool_version=output.tool_version,
                exit_code=output.exit_code,
                stdout_hash=sha256_text(output.stdout),
                stderr_hash=sha256_text(output.stderr),
                tex_hash=sha256_text(paper_tex),
                pdf_hash=(
                    sha256_bytes(output.pdf_bytes) if output.pdf_bytes is not None else None
                ),
                rendered_pdf_artifact_id=None,
                passed=passed,
                warnings=warnings,
                reason=(
                    "LaTeX render and layout checks passed."
                    if passed
                    else warnings[0]
                ),
            ),
            pdf_bytes=output.pdf_bytes,
        )


def build_latex_compile_check_report(
    *,
    config: LatexRenderConfig,
    render_result: LatexRenderResult | None,
    reasons: list[str] | None = None,
) -> LatexCompileCheckReport:
    """Build an aggregate compile/check report without scientific authority."""
    reasons = reasons or []
    warnings = list(render_result.warnings) if render_result is not None else []
    passed = render_result.passed if render_result is not None else not reasons
    return LatexCompileCheckReport(
        run_id=config.run_id,
        config=config,
        render_result=render_result,
        passed=passed,
        warnings=warnings,
        reasons=reasons,
    )


def _subprocess_latex_runner(
    paper_tex: str,
    config: LatexRenderConfig,
) -> LatexRunnerOutput:
    with tempfile.TemporaryDirectory(prefix="factori-latex-") as tmp:
        workdir = Path(tmp)
        tex_path = workdir / "paper.tex"
        tex_path.write_text(paper_tex, encoding="utf-8", newline="\n")
        command = [
            str(config.latex_executable),
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_path.name,
        ]
        completed = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
        if completed.returncode == 0:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                check=False,
            )
        pdf_path = workdir / "paper.pdf"
        return LatexRunnerOutput(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            pdf_bytes=pdf_path.read_bytes() if pdf_path.is_file() else None,
        )


__all__ = [
    "LatexRenderError",
    "LatexRenderer",
    "LatexRenderedDocument",
    "LatexRunnerOutput",
    "build_latex_compile_check_report",
]
