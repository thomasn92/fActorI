# Canonical Commands

Run commands from the repository root.

## Environment

```bash
uv sync --dev
```

## Protocol Contracts

Export the checked-in language-neutral JSON Schemas and deterministic examples:

```bash
uv run factori export-protocols
```

Verify that generated files are current without rewriting them:

```bash
uv run factori export-protocols --check
```

Validate deterministic example payloads against exported JSON Schemas:

```bash
uv run factori validate-protocol-examples
uv run factori validate-protocol-examples --json
```

An isolated consumer export can use `--output-dir <path>/jsonschema`. Protocol export is outside
run provenance: it creates no ledger commits, touches no run artifact manifests, and produces no
verification evidence.

Compare two exported schema versions without writing either directory:

```bash
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --json --fail-on-breaking
```

The checker is conservative. Unknown composition or reference changes require manual review. The
optional-property removal policy is breaking because generated consumers may depend on the field.

Check whether the protocol version bump is sufficient for a schema change:

```bash
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --old-version 0.1.0 \
  --new-version 0.8.0
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --json
```

Protocol validation, compatibility, and version checks are read-only developer-contract commands.
They do not create run artifacts, ledger commits, or verification evidence.

Schema package maintenance has no runtime command. Public callers should continue to import from
`factori.schemas`; validate schema refactors with `export-protocols --check`,
`validate-protocol-examples`, pytest, and Ruff.

Selected CLI commands now delegate to typed library entry points under `factori.commands`. The user
commands and output remain the compatibility surface.

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
uv run factori run-all --run-id demo-g --domain "human geography" --dry-run
uv run factori plan-run --run-id demo-h --domain "human geography" --json
```

Mutating commands fail if their completion artifacts already exist. Resume safely by selecting a
later stage, skip completed stages explicitly, or force only under the force-aware policy:

```bash
uv run factori run-all --run-id demo --domain "human geography" \
  --rerun-policy skip-if-complete
uv run factori run-stage-a --run-id demo --domain "human geography" \
  --rerun-policy allow-if-forced --force
uv run factori validate-ledger-tip --run-id demo
```

`fail-if-exists` is the default for all mutating stage commands and `run-all`. Read-only commands
remain rerunnable. `validate-ledger-tip` reports hash-chain failures, broken parent links, forks, multiple tips, and
repeated mutating-stage start markers without changing the ledger or artifact manifest.

`run-all` calls existing stage functions directly. Its pipeline report is hashed and ledgered;
replay and diagnostics remain read-only, and their optional reports remain outside provenance.
When `--start-at` is used, `run-all` first validates the requested resume point against explicit
checkpoint artifacts and blocks before mutation if prerequisites are missing.
Dry-run planning is read-only and reports selected stages, blockers, and expected outputs without
creating artifacts or ledger commits.

Equivalent individual commands:

```bash
uv run factori run-stage-a --run-id demo --domain "human geography"
uv run factori run-stage-b --run-id demo
uv run factori select-stage-c --run-id demo
uv run factori run-stage-c --run-id demo
uv run factori run-stage-c --run-id demo \
  --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-stage-c --run-id demo \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
uv run factori synthesize-abstract --run-id demo
uv run factori plan-manuscript --run-id demo
uv run factori critique-paper-shape --run-id demo
uv run factori critique-paper-shape --run-id demo --write-report
uv run factori critique-paper-shape --run-id demo --json
uv run factori generate-section-draft --run-id demo --section-id introduction
uv run factori generate-section-draft --run-id demo --section-id introduction --write-report
uv run factori generate-section-draft --run-id demo --section-id introduction --json
uv run factori draft-manuscript --run-id demo
uv run factori draft-manuscript --run-id demo --write-report
uv run factori draft-manuscript --run-id demo --json
uv run factori build-citation-registry --run-id demo
uv run factori build-citation-registry --run-id demo --write-report
uv run factori build-citation-registry --run-id demo --json
uv run factori draft-manuscript --run-id demo --include-citations
uv run factori export-latex --run-id demo
uv run factori export-latex --run-id demo --write-report
uv run factori export-latex --run-id demo --json
uv run factori export-latex --run-id demo \
  --render-check --allow-external-tools --latex-executable pdflatex
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
uv run factori run-all --run-id demo --domain "human geography" --dry-run
uv run factori plan-run --run-id demo --domain "human geography"
uv run factori plan-run --run-id demo --domain "human geography" --json
uv run factori inspect-hygiene --run-id demo
uv run factori inspect-hygiene --run-id demo --write-report
uv run factori inspect-hygiene --run-id demo --json
uv run factori plan-hygiene-remediation --run-id demo
uv run factori plan-hygiene-remediation --run-id demo --write-report
uv run factori plan-hygiene-remediation --run-id demo --json
uv run factori show-adapters
uv run factori adapters
```

The default adapter path is fake and makes no external calls. Gated OpenAI adapters are available
for Stage A candidate proposal and Stage B structural review. Each requires explicit backend/use,
external-call permission, and an API key. Models are configurable because availability can vary by
account:

```bash
OPENAI_API_KEY="<key>" uv run factori show-adapters \
  --backend openai --allow-external-calls --llm-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-stage-a \
  --run-id llm-demo --domain "human geography" --method "optimal transport" \
  --adapter-backend openai --allow-external-calls --llm-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-all \
  --run-id llm-pipeline --domain "human geography" --method "optimal transport" \
  --adapter-backend openai --allow-external-calls --llm-model gpt-5-mini
```

Stage B LLM reviewers provide critique and scores only. They have no verification, publication,
proof, experiment, retrieval, or human-approval authority:

```bash
OPENAI_API_KEY="<key>" uv run factori show-adapters \
  --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-all \
  --run-id reviewer-pipeline --domain "human geography" \
  --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
```

Prose drafting is separately gated. The fake backend is default and produces placeholder section
text from approved manuscript contracts. `generate-section-draft` drafts one section. `draft-manuscript`
uses the same prose adapter section by section and assembles a complete Markdown draft. Neither
command can create claims, invent citations or bibliography entries, create evidence, upgrade
labels, generate polished prose, produce LaTeX evidence, or claim publication readiness:

```bash
uv run factori generate-section-draft \
  --run-id demo --section-id introduction
uv run factori generate-section-draft \
  --run-id demo --section-id introduction --write-report
uv run factori draft-manuscript --run-id demo --write-report
uv run factori build-citation-registry --run-id demo --write-report
uv run factori draft-manuscript --run-id demo --include-citations --write-report
OPENAI_API_KEY="<key>" uv run factori generate-section-draft \
  --run-id demo --section-id introduction \
  --prose-backend openai --allow-external-calls --prose-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori draft-manuscript \
  --run-id demo --prose-backend openai --allow-external-calls --prose-model gpt-5-mini
```

LaTeX export is presentation/export only. It converts a complete Markdown manuscript draft into
`paper.tex`, bibliography placeholders, a source map, and safety/export reports when
`--write-report` is used. Render checks are optional and fail closed unless external tools are
explicitly enabled and a LaTeX executable is configured:

```bash
uv run factori export-latex --run-id demo --write-report
uv run factori export-latex --run-id demo --json
uv run factori export-latex --run-id demo \
  --render-check --allow-external-tools --latex-executable pdflatex
```

Real OpenAlex retrieval is separately gated and used only for Stage B literature context and
bounded adequacy. It does not prove novelty or complete literature coverage:

```bash
OPENALEX_API_KEY="<key>" uv run factori show-adapters \
  --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
OPENALEX_API_KEY="<key>" uv run factori retrieval-adequacy-demo \
  --query "human geography optimal transport" \
  --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
OPENALEX_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
```

Real proof verification is separately gated and used only for Stage C mathematical branches. It
requires an explicitly selected local proof backend, external-tool permission, and an executable.
Default runs never execute Lean or any proof tool:

```bash
uv run factori show-adapters \
  --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-stage-c \
  --run-id demo --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-all \
  --run-id proof-pipeline --domain "human geography" \
  --proof-backend lean --allow-external-tools --proof-executable lean
```

Local synthetic experiment execution is separately gated and used only for Stage C SyntheticOnly
branches. It requires explicit external-tool permission and an experiment runner. It never supports
real-world empirical validation:

```bash
uv run factori show-adapters \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
uv run factori run-stage-c \
  --run-id demo --experiment-backend local_synthetic \
  --allow-external-tools --experiment-runner local-runner
uv run factori run-all \
  --run-id synthetic-pipeline --domain "human geography" \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
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

`run-all --dry-run` and `plan-run` are read-only planning commands. They mirror run-all stage
ordering and resume validation but do not execute stages, write dry-run reports, append ledger
commits, or update artifact manifests.

`inspect-hygiene` checks manifest/file consistency, artifact hashes, evidence boundaries,
non-provenance report placement, duplicate outputs, stale sidecars/cache files, and unexpected run
contents. It never deletes or repairs files. With `--write-report`, it writes only marked
non-provenance files under `runs/<run_id>/hygiene/`; that directory is excluded from normal
artifact manifests and no ledger commit is created.

`plan-hygiene-remediation` runs the read-only hygiene inspection and maps each finding to a
conservative recommendation with an explicit risk and optional producing-stage rerun command. It
does not execute any recommendation or rewrite a manifest. With `--write-report`, it writes only
marked non-provenance plan files under `runs/<run_id>/hygiene/` and creates no ledger commit.

`critique-paper-shape` is read-only by default. It checks the narrative manuscript contract and
paper-shape diagnostics after `plan-manuscript`. With `--write-report`, it writes hashed, ledgered
manuscript-quality context artifacts under `runs/<run_id>/reports/`; these are not verification
evidence and cannot change scientific labels.

`show-adapters` (alias `adapters`) prints the active registry without invoking it. The default is
`adapter_backend=fake` and `allow_external_calls=false`. The `openai` backend fails before any
network call unless external calls are explicitly allowed and an API key is present.
The `openalex` retrieval backend has the same fail-closed behavior and uses
`OPENALEX_API_KEY`; retrieval artifacts remain non-verification context.
The `lean` proof backend fails before tool execution unless external tools are explicitly allowed
and a proof executable is configured. The `local_synthetic` experiment backend fails before tool
execution unless external tools are explicitly allowed and an experiment runner is configured.
The output also includes provider-neutral capability descriptors for the fake, OpenAI candidate,
OpenAI reviewer, OpenAI prose, OpenAlex retrieval, Lean proof, and local synthetic experiment
backends.

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

Persistence tests cover atomic replacement, failed-write cleanup, final-byte hashes, newline
normalization, storage protocol conformance, and fixed-clock pipeline timestamps. No separate CLI
configuration is required; normal commands continue to use the UTC system clock.

Default commands do not call external APIs, real Lean, experiment runners, Docker, a server, or a
frontend. Only the explicitly gated Stage A OpenAI, Stage B OpenAI reviewer, Stage B OpenAlex, and
Stage C Lean/local synthetic commands above may call an external API or local external tool.
