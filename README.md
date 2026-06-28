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
- deterministic section-by-section Markdown manuscript drafting with strict prose safety checks;
- deterministic citation registry and bounded literature-positioning integration for Markdown
  drafts, with citation-safety checks and no novelty-proof authority;
- deterministic LaTeX export from complete Markdown drafts, with bibliography placeholders,
  source maps, safety checks, and optional gated render diagnostics;
- deterministic paper critic and one safe fake revision pass over Markdown/LaTeX artifacts,
  covering paper shape, citation safety, evidence boundaries, source maps, and section-level
  revision planning;
- deterministic full-paper package generation that chains citation registry construction,
  manuscript drafting, LaTeX export, paper critique, and optional safe fake revision/re-export;
- explicit gated end-to-end LLM-assisted paper orchestration with call/cost budget checks,
  secret-safe accounting, and fake smoke mode;
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
- an explicitly gated local Lean proof adapter for Stage C mathematical branches, with proof
  contracts, trace/result hashes, safety checks, and no default proof-tool execution;
- an explicitly gated local synthetic experiment runner for Stage C SyntheticOnly branches, with
  contracts, input/output/trace hashes, safety checks, and no default experiment-tool execution;
- an explicitly gated OpenAI prose adapter, with section contracts, safety checks, optional complete
  Markdown draft assembly, ledgered non-evidence prose artifacts when requested, and no default
  network access;
- versioned language-neutral JSON Schema contracts and deterministic interoperability examples;
- conservative read-only protocol compatibility and schema-change classification;
- server-facing run-control, adapter I/O, manifest, and enum protocol exports with JSON
  Schema-level example validation and explicit protocol versioning checks;
- fail-closed mutating-stage rerun policies and read-only ledger tip/fork validation;
- pytest coverage for the MVP invariants;
- Ruff configuration.

Not implemented yet: LangGraph orchestration, autonomous real-LLM research synthesis, polished full-paper writing,
complete or claim-verifying literature coverage, always-on Lean integration, real empirical
experiments, Docker, FastAPI, hard PDF-generation dependencies, publication-ready LaTeX, or a
frontend.

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
uv run factori generate-section-draft --run-id demo --section-id introduction
uv run factori generate-section-draft --run-id demo --section-id introduction --write-report
uv run factori draft-manuscript --run-id demo
uv run factori draft-manuscript --run-id demo --write-report
uv run factori build-citation-registry --run-id demo
uv run factori build-citation-registry --run-id demo --write-report
uv run factori draft-manuscript --run-id demo --include-citations
uv run factori export-latex --run-id demo
uv run factori export-latex --run-id demo --write-report
uv run factori export-latex --run-id demo --json
uv run factori export-latex --run-id demo --render-check \
  --allow-external-tools --latex-executable pdflatex
uv run factori critique-paper --run-id demo
uv run factori critique-paper --run-id demo --write-report
uv run factori revise-paper --run-id demo
uv run factori revise-paper --run-id demo --apply-safe-fake-revision --write-report
uv run factori generate-paper --run-id demo
uv run factori generate-paper --run-id demo --write-report
uv run factori generate-paper --run-id demo --apply-safe-fake-revision --write-report
uv run factori run-llm-paper --run-id llm-fake --domain "human geography" \
  --candidate-backend fake --reviewer-backend fake --prose-backend fake \
  --apply-safe-fake-revision --write-report
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
  --old-version 0.1.0 --new-version 0.13.0
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
retrieval and OpenAI structural-review paths. Stage C has separately gated local Lean proof and
local synthetic experiment paths. OpenAI prose drafting is also separately gated. All
are disabled by default.

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
metadata and abstract context with `OPENALEX_API_KEY`. A separately gated `lean` proof backend
supports Stage C mathematical branches through a local executable only when
`allow_external_tools=true`. A separately gated `local_synthetic` backend supports Stage C
SyntheticOnly branches through an explicitly configured runner only when
`allow_external_tools=true`. A separately gated `openai` prose backend drafts planned sections from
approved contracts only when `allow_external_calls=true` and `OPENAI_API_KEY` is present.
Human-review adapters remain fake.

LLM output is validated locally, then passes through the existing data gate, scoring, deduplication,
artifact store, and ledger. Requests, raw responses, parse reports, and proposals are not
verification evidence.

LLM reviewer output is also validated locally and may affect only existing Stage B reviewer scores
and disagreement routing. It cannot assign verification labels, approve publication, establish
proof or experiment success, or turn bounded retrieval context into a literature-coverage claim.

OpenAlex retrieval output is normalized, source-hashed, and ledgered through Stage B. It supports
only bounded retrieval adequacy and literature context. It does not prove novelty, complete
coverage, claim correctness, or external-review readiness.

Citation registries and literature-positioning reports are built from retrieval metadata.
Citation markers are allowed only when they match registry records. Bibliography entries are
placeholders backed by source provenance; they are not proof evidence, experiment evidence, human
approval, scientific validation, or novelty proof.

Lean proof output is accepted only through explicit proof contracts, local-tool traces, proof
result hashes, and safety validation. LLM output, reviewer reports, retrieval records, Markdown,
LaTeX, and paper artifacts cannot justify `LeanVerified`.

Local synthetic experiment output is accepted only through explicit experiment contracts,
runner-input/output artifacts, local-tool traces, result hashes, and safety validation. It can
support only `SyntheticExperimentVerified` for SyntheticOnly claims and cannot justify
`RealDataExperimentVerified`, empirical validation, or mathematical proof labels.

Generated section prose and complete Markdown drafts are accepted only as manuscript/prose context.
They may be written as hashed, ledgered drafting artifacts, but they cannot create claims,
invent citations or bibliography entries, create proof evidence, experiment evidence, retrieval
evidence, human approval, empirical validation, novelty proof, or verification-label upgrades.

Paper critic and revision artifacts are manuscript-quality context only. `critique-paper` checks
paper shape, citation safety, evidence-boundary language, LaTeX/source-map coverage, and appendix
presence. `revise-paper --apply-safe-fake-revision --write-report` can write a deterministic
conservative revision that downgrades unsafe language and inserts missing warnings/placeholders.
It cannot invent citations, mutate claim/evidence tables, create evidence, upgrade labels, or
claim publication readiness.

`generate-paper` chains the existing non-evidence manuscript workflow for an existing run:
citations, literature positioning, Markdown drafting, LaTeX export, and paper critique by default.
Revision and render diagnostics remain explicitly gated. Full-paper generation artifacts are
presentation/context/export artifacts only; they cannot create or upgrade evidence labels, mutate
claim/evidence tables, invent citations, or claim publication readiness.

`evaluate-paper-release` checks a generated bundle for internal human-review handoff readiness:

```bash
uv run factori evaluate-paper-release --run-id demo
uv run factori evaluate-paper-release --run-id demo --write-report
```

`ReadyForHumanReview` is not peer review, acceptance, scientific validation, verification
evidence, or publication readiness.

`run-llm-paper` is the explicit end-to-end LLM-assisted orchestration command. Fake mode remains
local and deterministic:

```bash
uv run factori run-llm-paper --run-id llm-fake --domain "human geography" \
  --candidate-backend fake --reviewer-backend fake --prose-backend fake \
  --apply-safe-fake-revision --write-report
```

Real mode requires explicit OpenAI backends, `--allow-external-calls`, credentials, and a budget
before any network call can be attempted:

```bash
OPENAI_API_KEY="<key>" uv run factori run-llm-paper \
  --run-id llm-real --domain "human geography" \
  --allow-external-calls \
  --candidate-backend openai --reviewer-backend openai --prose-backend openai \
  --candidate-model gpt-5-mini --reviewer-model gpt-5-mini --prose-model gpt-5-mini \
  --max-total-calls 50 --max-estimated-cost-usd 5.00 --rate-limit-per-minute 10 \
  --apply-safe-fake-revision --write-report
```

Recommended live-smoke ladder:

```bash
export OPENAI_API_KEY="..."

uv run factori run-llm-paper \
  --run-id live-smoke-preflight \
  --domain "human geography" \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend openai --reviewer-model gpt-5.4-mini \
  --prose-backend openai --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 29 --max-estimated-cost-usd 1.00 \
  --preflight-only --json

uv run factori run-llm-paper \
  --run-id live-candidate-smoke-004 \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend fake --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 3 --max-candidate-generation-calls 3 \
  --max-estimated-cost-usd 0.20 \
  --write-report --json

uv run factori run-llm-paper \
  --run-id live-candidate-budget-block \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend fake --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 1 --max-candidate-generation-calls 1 \
  --max-estimated-cost-usd 0.20 \
  --write-report --json

uv run factori run-llm-paper \
  --run-id live-smoke-002 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend openai --reviewer-model gpt-5.4-mini \
  --prose-backend openai --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 50 --max-estimated-cost-usd 1.00 \
  --generate-paper --evaluate-release --write-report --json
```

OpenAI 4xx/5xx diagnostics include status, operation, backend/provider, redacted URL, selected
model, request/prompt hashes, and a sanitized truncated error-body excerpt. They never include API
keys or Authorization headers.
OpenAI structured outputs use an adapter-local strict schema copy: every object property is listed
in `required`, optional values are nullable, and public fActorI protocol schemas remain separate.
`--llm-scope candidate-only` is an isolated live-smoke path: it runs Stage A candidate generation
only and forces paper generation, LaTeX export, critique/revision, and release evaluation off.
Runtime LLM budget guards authorize each candidate/reviewer/prose transport call before the
external request; a blocked call is recorded as `Blocked` with `external_call_performed=false`.

`--llm-scope reviewer-only` runs Stage A and the Stage B reviewer path only. It disables Stage C,
paper generation, release evaluation, LaTeX export, critique, and revision. Preflight plans one
review request per deterministic Stage B child: the current four-survivor/four-child maximum is 16
review calls, or 19 total calls for the three-call human-geography candidate path. A runtime budget
failure in full-paper mode now blocks orchestration before paper generation.

Current candidate-only live smoke:

```bash
uv run factori run-llm-paper \
  --run-id live-candidate-smoke-004 \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai \
  --candidate-model gpt-5.4-mini \
  --reviewer-backend fake \
  --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 3 \
  --max-candidate-generation-calls 3 \
  --max-estimated-cost-usd 0.20 \
  --write-report \
  --json
```

The orchestration report, budget report, call accounting, and safety report are context/audit
artifacts only. They cannot create evidence, upgrade labels, or imply publication readiness.

The deterministic golden smoke workflow exercises the complete scaffold without network or
external tools:

```bash
uv run factori run-all --run-id golden-paper --domain "human geography"
uv run factori generate-paper --run-id golden-paper \
  --apply-safe-fake-revision --reexport-latex-after-revision --write-report
uv run factori evaluate-paper-release --run-id golden-paper --write-report
uv run factori export-protocols --check
uv run factori validate-protocol-examples
```

The corresponding test pins structural outputs, replay, hygiene, audit, and protocol compatibility.
It is a regression fixture, not scientific validation or publication readiness.

LaTeX export is accepted only as presentation/export context. `export-latex --write-report` writes
content-hashed LaTeX source, bibliography placeholders, source maps, export reports, and safety
reports. `--render-check` is optional and requires `--allow-external-tools` plus a configured LaTeX
executable. LaTeX files and rendered PDFs cannot create claims, mutate claim/evidence tables,
upgrade labels, prove publication readiness, or justify proof, experiment, retrieval, human-review,
or scientific-validation evidence.

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
