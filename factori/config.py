"""Configuration constants for the deterministic MVP."""

from __future__ import annotations

from pathlib import Path

RUNS_DIR = "runs"
RUN_SUBDIRECTORIES = (
    "candidates",
    "scores",
    "reports",
    "literature",
    "lean",
    "experiments",
    "logs",
    "latex",
    "research_object",
)
DEFAULT_ROOT = Path(".")
DEFAULT_RUN_ID = "run-0001"
LEDGER_FILENAME = "ledger.sqlite"
