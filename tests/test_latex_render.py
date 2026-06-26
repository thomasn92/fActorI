from __future__ import annotations

import pytest

from factori.latex_render import (
    LatexRenderer,
    LatexRenderError,
    LatexRunnerOutput,
    build_latex_compile_check_report,
)
from factori.schemas import LatexRenderConfig


def test_render_check_fails_closed_when_external_tools_disabled() -> None:
    renderer = LatexRenderer(runner=_successful_runner)

    with pytest.raises(LatexRenderError, match="External render tools are disabled"):
        renderer.render(
            "\\documentclass{article}\\begin{document}x\\end{document}",
            LatexRenderConfig(
                run_id="run-1",
                render_check_enabled=True,
                allow_external_tools=False,
                latex_executable="pdflatex",
            ),
        )


def test_render_check_fails_when_executable_missing() -> None:
    renderer = LatexRenderer(runner=_successful_runner)

    with pytest.raises(LatexRenderError, match="executable is not configured"):
        renderer.render(
            "\\documentclass{article}\\begin{document}x\\end{document}",
            LatexRenderConfig(
                run_id="run-1",
                render_check_enabled=True,
                allow_external_tools=True,
                latex_executable=None,
            ),
        )


def test_render_success_with_injected_runner_is_deterministic() -> None:
    renderer = LatexRenderer(runner=_successful_runner)
    config = LatexRenderConfig(
        run_id="run-1",
        render_check_enabled=True,
        allow_external_tools=True,
        latex_executable="pdflatex",
    )

    first = renderer.render(_tex(), config)
    second = renderer.render(_tex(), config)

    assert first == second
    assert first.passed
    assert first.exit_code == 0
    assert first.pdf_hash is not None
    assert first.is_verification_evidence is False
    assert first.creates_scientific_validation is False
    assert first.implies_publication_readiness is False


def test_render_failure_is_captured_deterministically() -> None:
    renderer = LatexRenderer(runner=_failing_runner)
    config = LatexRenderConfig(
        run_id="run-1",
        render_check_enabled=True,
        allow_external_tools=True,
        latex_executable="pdflatex",
    )

    result = renderer.render(_tex(), config)

    assert not result.passed
    assert result.exit_code == 1
    assert result.pdf_hash is None
    assert result.reason == "LaTeX render check failed."
    assert result.warnings == ["LaTeX render check failed."]


def test_compile_check_report_does_not_imply_scientific_validation() -> None:
    renderer = LatexRenderer(runner=_successful_runner)
    config = LatexRenderConfig(
        run_id="run-1",
        render_check_enabled=True,
        allow_external_tools=True,
        latex_executable="pdflatex",
    )
    render = renderer.render(_tex(), config)

    report = build_latex_compile_check_report(config=config, render_result=render)

    assert report.passed
    assert report.is_verification_evidence is False
    assert report.creates_scientific_validation is False
    assert report.implies_publication_readiness is False


def test_render_skipped_result_is_presentation_only() -> None:
    renderer = LatexRenderer()
    result = renderer.render(
        _tex(),
        LatexRenderConfig(run_id="run-1", render_check_enabled=False),
    )

    assert result.passed
    assert result.reason == "Render check skipped."
    assert result.warnings == ["LaTeX render check was not requested."]
    assert result.is_verification_evidence is False


def _successful_runner(_paper_tex: str, _config: LatexRenderConfig) -> LatexRunnerOutput:
    return LatexRunnerOutput(
        exit_code=0,
        stdout="ok",
        stderr="",
        pdf_bytes=b"%PDF fake",
        tool_version="fake-pdflatex-1.0",
    )


def _failing_runner(_paper_tex: str, _config: LatexRenderConfig) -> LatexRunnerOutput:
    return LatexRunnerOutput(
        exit_code=1,
        stdout="",
        stderr="missing brace",
        pdf_bytes=None,
        tool_version="fake-pdflatex-1.0",
    )


def _tex() -> str:
    return "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
