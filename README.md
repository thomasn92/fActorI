# fActorI

This repository contains the Milestone 0-2 deterministic foundation for fActorI, based on
`fActori_updated_data_regime.tex`.

Implemented in this milestone:

- strict Pydantic schemas for core research entities;
- a local SQLite append-only ledger with deterministic commit hashes;
- a local filesystem artifact store under `runs/<run_id>/`;
- a minimal Typer CLI;
- pytest coverage for the MVP invariants;
- Ruff configuration.

Not implemented yet: LangGraph orchestration, Lean integration, real model calls, real literature
retrieval, experiments, Docker, FastAPI, or a frontend.

## Install

```bash
uv sync --dev
```

## Test And Lint

```bash
uv run pytest
uv run ruff check .
```

## CLI

```bash
uv run factori init-run --run-id demo
uv run factori add-candidate --run-id demo --candidate-id candidate-001
uv run factori write-artifact --run-id demo --artifact-id report-001 --kind report --format markdown
uv run factori run-stage-a --run-id demo --domain "human geography"
uv run factori run-stage-b --run-id demo
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
```

All commands are local and deterministic. They do not call models, retrieval services, Lean,
experiment runners, Docker, servers, or UI code.
