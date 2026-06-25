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

Public schema imports should use `factori.schemas`. The schema definitions are internally grouped
under `factori/schemas/`, but the package re-exports preserve existing imports such as
`from factori.schemas import Candidate`.

Language-neutral developer contracts are documented in [`protocols/README.md`](protocols/README.md).
Compatibility rules are documented in
[`protocols/compatibility.md`](protocols/compatibility.md), with version bump rules in
[`protocols/versioning.md`](protocols/versioning.md) and server-readiness notes in
[`protocols/server-readiness.md`](protocols/server-readiness.md).

Implemented so far:

- strict Pydantic schemas for core research entities;
- grouped schema modules with stable `factori.schemas` compatibility re-exports;
- typed library entry points for selected CLI commands, keeping CLI output compatible;
- a local SQLite append-only ledger with deterministic commit hashes;
- a local filesystem artifact store under `runs/<run_id>/` with atomic replacement, UTF-8/LF
  normalization, final-byte hashing, and best-effort durability sync;
- small runtime-checkable ledger, artifact-store, and clock protocols with fixed-clock test support;
- a minimal Typer CLI;
- deterministic fake Stage 0 opportunity discovery and Stage A candidate ranking;
- deterministic fake Stage B structural validation;
- deterministic Strategic Questioner, Autonomy Contract, stagnation, retrieval adequacy, and
  runtime summary skeletons;
- deterministic Stage B-to-C red-team filtering and Stage C candidate selection;
- deterministic fake Stage C verification labeling and evidence-boundary checks;
- deterministic Abstract Synthesis skeleton and final nucleus selection;
- deterministic manuscript planning skeleton with claim/evidence tables;
- deterministic narrative manuscript contract and paper-shape critique;
- deterministic draft skeleton and manuscript checklist generation;
- deterministic research object packaging and audit manifests;
- deterministic final-paper assembly skeleton;
- deterministic final audit and release gate;
- deterministic export-preparation contracts and plans;
- read-only deterministic replay verification for completed runs;
- read-only deterministic provenance diagnostics and safe rerun recommendations;
- read-only deterministic cross-run comparison and regression diagnostics;
- canonical direct one-command deterministic pipeline orchestration;
- read-only checkpoint/status inspection and stricter run-all resume validation;
- read-only pipeline dry-run planning for run-all options and expected outputs;
- read-only run output hygiene inspection for orphaned, stale, duplicate, or leaked files;
- deterministic non-executing hygiene remediation plans with explicit risk levels and rerun advice;
- explicit LLM, retrieval, proof, experiment, prose, and human-review adapter interfaces with
  deterministic fake defaults;
- an explicitly gated OpenAI adapter for Stage A candidate proposal only, with strict local parsing
  and ledgered non-evidence request/response traces;
- an explicitly gated OpenAlex adapter for Stage B source metadata/abstract retrieval, with source
  hashes, ledgered context artifacts, and bounded adequacy that does not prove novelty;
- an explicitly gated OpenAI reviewer adapter for Stage B structural critique, with strict local
  safety checks, ledgered context artifacts, and no verification or publication authority;
- versioned language-neutral JSON Schema contracts and deterministic interoperability examples;
- conservative read-only protocol compatibility and schema-change classification;
- server-facing run-control, adapter I/O, manifest, and enum protocol exports with JSON
  Schema-level example validation and explicit protocol versioning checks;
- fail-closed mutating-stage rerun policies and read-only ledger tip/fork validation;
- pytest coverage for the MVP invariants;
- Ruff configuration.

Not implemented yet: LangGraph orchestration, real LLM synthesis/writing, complete or
claim-verifying literature coverage, Lean integration, real experiments, Docker, FastAPI, full
manuscript synthesis, LaTeX paper generation, or a frontend.

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

Canonical full deterministic run:

```bash
uv run factori run-all --run-id demo --domain "human geography"
```

The runner supports `--method`, `--root`, `--stop-after`, `--start-at`, `--skip-replay`,
`--run-diagnostics`, optional non-provenance replay/diagnostic report flags, `--fail-fast`, and an
explicit `--rerun-policy`.
Replay and diagnostics remain read-only within the orchestrated run. A repeated full run with the
same run ID fails clearly by default. `skip-if-complete` explicitly skips completed stages;
`allow-if-forced --force` permits deliberate reruns. `--start-at` is validated against explicit
checkpoint artifacts before any resumed stage runs. Use `--dry-run` or `plan-run` to inspect the
planned stages and blockers without executing or writing anything.

Individual stage and inspection commands:

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
uv run factori critique-paper-shape --run-id demo
uv run factori critique-paper-shape --run-id demo --write-report
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
uv run factori plan-run --run-id demo --domain "human geography" --json
uv run factori inspect-hygiene --run-id demo
uv run factori inspect-hygiene --run-id demo --write-report
uv run factori inspect-hygiene --run-id demo --json
uv run factori plan-hygiene-remediation --run-id demo
uv run factori plan-hygiene-remediation --run-id demo --write-report
uv run factori plan-hygiene-remediation --run-id demo --json
uv run factori show-adapters
uv run factori export-protocols
uv run factori export-protocols --check
uv run factori validate-protocol-examples
uv run factori check-protocol-compat --old-dir old/jsonschema --new-dir new/jsonschema
uv run factori check-protocol-version --old-dir old/jsonschema --new-dir new/jsonschema \
  --old-version 0.1.0 --new-version 0.3.0
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
uv run factori validate-ledger-tip --run-id demo
```

Default commands are local and deterministic. They do not call models, retrieval services, Lean,
experiment runners, Docker, servers, or UI code. The gated Stage A OpenAI path described below is
one explicit exception and is never selected by default. Stage B also has separately gated OpenAlex
retrieval and OpenAI structural-review paths.

`inspect-hygiene` is read-only. Its optional reports are written under
`runs/<run_id>/hygiene/`, explicitly marked non-provenance/non-evidence/non-ledgered, and excluded
from normal artifact manifests. It never deletes, repairs, rewrites, or rehashes stored metadata.

`plan-hygiene-remediation` maps hygiene findings to conservative recommendations and deterministic
rerun commands when a producing stage is identifiable. It never executes cleanup, deletion,
quarantine, restoration, manifest regeneration, or reruns. Optional plans remain under `hygiene/`
and outside provenance.

`export-protocols` derives developer-facing JSON Schemas from existing typed models. Its `--check`
mode is read-only and suitable for CI. Protocol files are not run artifacts, ledger provenance, or
scientific evidence.

`check-protocol-compat` compares exported schema directories without modifying them. It detects
common breaking and widening changes and reports complex composition/reference changes as unknown
instead of claiming semantic compatibility.

`validate-protocol-examples` validates deterministic examples against exported JSON Schemas.
`check-protocol-version` enforces MAJOR/MINOR/PATCH rules for schema changes. Both commands are
developer-contract checks only; they do not create run artifacts or ledger commits.

Persistence writes use a same-directory temporary file, flush and fsync its bytes, then atomically
replace the final path. Commit and pipeline timestamps use `SystemClock` by default; tests and
embedded callers may inject `FixedClock` without changing CLI behavior.

## Adapter Interfaces

The adapter registry exposes `LLMClient`, `ReviewerClient`, `RetrievalClient`, `ProofVerifier`,
`ExperimentRunner`, `ProseGenerator`, and `HumanReviewClient`. It defaults to `fake` with external
calls disabled. Fake adapters use local deterministic templates and validators. The registry also
exposes provider-neutral capability descriptors and typed adapter errors so future providers can be
added without changing evidence or provenance rules. A provider-isolated
`openai` backend supports Stage A candidate proposal, and a separate explicit Stage B reviewer flag
uses the same provider transport for structural critique only. Both require external-call permission
plus `OPENAI_API_KEY`. A separately gated `openalex` retrieval backend supports Stage B source
metadata and abstract context with `OPENALEX_API_KEY`. Proof, experiment, prose, and human-review
adapters remain fake.

LLM output is validated locally, then passes through the existing data gate, scoring, deduplication,
artifact store, and ledger. Requests, raw responses, parse reports, and proposals are not
verification evidence.

LLM reviewer output is also validated locally and may affect only existing Stage B reviewer scores
and disagreement routing. It cannot assign verification labels, approve publication, establish
proof or experiment success, or turn bounded retrieval context into a literature-coverage claim.

OpenAlex retrieval output is normalized, source-hashed, and ledgered through Stage B. It supports
only bounded retrieval adequacy and literature context. It does not prove novelty, complete
coverage, claim correctness, or external-review readiness.

`show-adapters` prints both active adapter classes and provider capability metadata. Invalid
backend names, disabled external calls, missing credentials, capability mismatches, HTTP failures,
and malformed JSON responses use shared typed errors. Error strings are deterministic and must not
expose API keys or secrets.

## Narrative Paper Shape

`critique-paper-shape` checks whether the manuscript plan has a central message, explicit problem
framing, bounded literature positioning, simple model notation, one main result, purposeful
numerics, synthetic/empirical boundaries, and appendix allocation.

```bash
uv run factori critique-paper-shape --run-id demo
uv run factori critique-paper-shape --run-id demo --write-report
```

The critique is manuscript-quality context only. It is not proof evidence, experiment evidence,
retrieval evidence, human approval, or scientific validation.

```bash
OPENAI_API_KEY="<key>" uv run factori run-stage-a \
  --run-id llm-demo --domain "human geography" --method "optimal transport" \
  --adapter-backend openai --allow-external-calls --llm-model gpt-5-mini
```

```bash
OPENALEX_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
```

```bash
OPENAI_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
```
