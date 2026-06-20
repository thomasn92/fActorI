# Canonical Commands

Run commands from the repository root.

## Environment

```bash
uv sync --dev
```

## Deterministic Pipeline

Canonical one-command run:

```bash
uv run factori run-all --run-id demo --domain "human geography"
```

Common controls:

```bash
uv run factori run-all --run-id demo-a --domain "human geography" --method "optimal transport"
uv run factori run-all --run-id demo-b --domain "human geography" --stop-after run-stage-c
uv run factori run-all --run-id demo-b --domain "human geography" --start-at synthesize-abstract
uv run factori run-all --run-id demo-c --domain "human geography" --skip-replay
uv run factori run-all --run-id demo-d --domain "human geography" --run-diagnostics
uv run factori run-all --run-id demo-e --domain "human geography" --run-diagnostics \
  --write-replay-report --write-diagnostic-report
uv run factori run-all --run-id demo-f --domain "human geography" --fail-fast
```

`run-all` calls existing stage functions directly. Its pipeline report is hashed and ledgered;
replay and diagnostics remain read-only, and their optional reports remain outside provenance.
When `--start-at` is used, `run-all` first validates the requested resume point against explicit
checkpoint artifacts and blocks before mutation if prerequisites are missing.

Equivalent individual commands:

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
uv run factori compare-runs --baseline-run-id baseline --candidate-run-id candidate
uv run factori compare-runs --baseline-run-id baseline --candidate-run-id candidate --write-report
uv run factori status --run-id demo
uv run factori status --run-id demo --stage run-stage-b
uv run factori status --run-id demo --json
uv run factori validate-resume --run-id demo --start-at plan-manuscript
```

`replay-verify` is read-only. With `--write-report`, it writes only non-provenance files under
`runs/demo/replay/` and does not append ledger commits or update the artifact manifest.

`diagnose-run` explains available final-audit, release, export, and replay findings. It never
executes its recommended commands. With `--write-report`, it writes only non-provenance files under
`runs/demo/diagnostics/` and does not append ledger commits or update the artifact manifest.

`compare-runs` reads two completed runs and reports deterministic drift and regressions. With
`--write-report`, it writes only non-provenance files under `runs/<candidate>/comparisons/` and
does not append ledger commits or update either artifact manifest.

`status` and `validate-resume` are read-only checkpoint inspection commands. They do not append
ledger commits, update manifests, or write status reports.

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
