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
DEFAULT_ADAPTER_BACKEND = "fake"
DEFAULT_ALLOW_EXTERNAL_CALLS = False
DEFAULT_LLM_MODEL = "gpt-5-mini"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_REVIEWER_BACKEND = "fake"
DEFAULT_REVIEWER_MAX_OBJECTIONS = 5
DEFAULT_RETRIEVAL_BACKEND = "fake"
DEFAULT_RETRIEVAL_LIMIT = 5
OPENALEX_API_KEY_ENV = "OPENALEX_API_KEY"
DEFAULT_PROOF_BACKEND = "fake"
DEFAULT_ALLOW_EXTERNAL_TOOLS = False
DEFAULT_PROOF_TIMEOUT_SECONDS = 10
