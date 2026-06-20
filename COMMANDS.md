# Canonical Commands

Run commands from the repository root.

## Environment

```bash
uv sync --dev
```

## Deterministic Pipeline

```bash
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
```

`replay-verify` is read-only. With `--write-report`, it writes only non-provenance files under
`runs/demo/replay/` and does not append ledger commits or update the artifact manifest.

`diagnose-run` explains available final-audit, release, export, and replay findings. It never
executes its recommended commands. With `--write-report`, it writes only non-provenance files under
`runs/demo/diagnostics/` and does not append ledger commits or update the artifact manifest.

## Foundation and Inspection

```bash
uv run factori init-run --run-id demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
```

## Tests and Lint

Canonical `uv` commands:

```bash
uv run pytest
uv run ruff check .
```

Commands for an already-created local environment:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

No command in the deterministic pipeline calls external APIs, real Lean, real experiments,
Docker, a server, or a frontend.
