# fActorI

This repository contains the deterministic MVP foundation for fActorI, based on
`fActori_updated_data_regime.tex`.

## For coding agents

Start with:

1. [`AGENTS.md`](AGENTS.md)
2. [`CONTEXT.md`](CONTEXT.md)
3. [`ARCHITECTURE.md`](ARCHITECTURE.md)
4. [`MILESTONES.md`](MILESTONES.md)
5. [`MODULE_MAP.md`](MODULE_MAP.md)
6. [`COMMANDS.md`](COMMANDS.md)

The LaTeX specification is reference material only and should not be read in full unless required
by the task.

Implemented so far:

- strict Pydantic schemas for core research entities;
- a local SQLite append-only ledger with deterministic commit hashes;
- a local filesystem artifact store under `runs/<run_id>/`;
- a minimal Typer CLI;
- deterministic fake Stage 0 opportunity discovery and Stage A candidate ranking;
- deterministic fake Stage B structural validation;
- deterministic Strategic Questioner, Autonomy Contract, stagnation, retrieval adequacy, and
  runtime summary skeletons;
- deterministic Stage B-to-C red-team filtering and Stage C candidate selection;
- deterministic fake Stage C verification labeling and evidence-boundary checks;
- deterministic Abstract Synthesis skeleton and final nucleus selection;
- deterministic manuscript planning skeleton with claim/evidence tables;
- deterministic draft skeleton and manuscript checklist generation;
- deterministic research object packaging and audit manifests;
- deterministic final-paper assembly skeleton;
- deterministic final audit and release gate;
- deterministic export-preparation contracts and plans;
- read-only deterministic replay verification for completed runs;
- read-only deterministic provenance diagnostics and safe rerun recommendations;
- pytest coverage for the MVP invariants;
- Ruff configuration.

Not implemented yet: LangGraph orchestration, Lean integration, real model calls, real literature
retrieval, real experiments, Docker, FastAPI, full manuscript synthesis, LaTeX paper generation,
or a frontend.

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
uv run factori select-stage-c --run-id demo
uv run factori run-stage-c --run-id demo
uv run factori synthesize-abstract --run-id demo
uv run factori plan-manuscript --run-id demo
uv run factori build-draft-skeleton --run-id demo
uv run factori package-research-object --run-id demo
uv run factori assemble-paper-skeleton --run-id demo
uv run factori final-audit --run-id demo
uv run factori prepare-export --run-id demo
uv run factori replay-verify --run-id demo
uv run factori replay-verify --run-id demo --write-report
uv run factori diagnose-run --run-id demo
uv run factori diagnose-run --run-id demo --write-report
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
```

All commands are local and deterministic. They do not call models, retrieval services, Lean,
experiment runners, Docker, servers, or UI code.
