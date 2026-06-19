"""Read-only replay API and optional non-provenance report writer."""

from __future__ import annotations

from pathlib import Path

from factori.config import DEFAULT_ROOT
from factori.hashing import canonical_json
from factori.reports import render_replay_verification_report_markdown
from factori.run_verifier import (
    ReplayVerificationError,
    replay_verify_run,
    summarize_replay_verification,
)
from factori.schemas import ReplayVerificationReport


def write_replay_report(
    *,
    run_id: str,
    report: ReplayVerificationReport,
    root: str | Path = DEFAULT_ROOT,
) -> tuple[Path, Path]:
    """Write optional replay reports outside normal provenance and evidence."""
    replay_path = Path(root) / "runs" / run_id / "replay"
    replay_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "report": report.model_dump(mode="json"),
    }
    json_path = replay_path / "replay-verification-report.json"
    markdown_path = replay_path / "replay-verification-report.md"
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    markdown = "\n".join(
        [
            "---",
            "not_provenance: true",
            "not_evidence: true",
            "not_ledgered: true",
            "---",
            "",
            render_replay_verification_report_markdown(replay_report=report),
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


__all__ = [
    "ReplayVerificationError",
    "replay_verify_run",
    "summarize_replay_verification",
    "write_replay_report",
]
