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
`--run-diagnostics`, optional non-provenance replay/diagnostic report flags, and `--fail-fast`.
Replay and diagnostics remain read-only within the orchestrated run. A repeated full run with the
same run ID fails clearly; `--start-at` is validated against explicit checkpoint artifacts before
any resumed stage runs. Use `--dry-run` or `plan-run` to inspect the planned stages and blockers
without executing or writing anything.

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
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
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

## Adapter Interfaces

The adapter registry exposes `LLMClient`, `ReviewerClient`, `RetrievalClient`, `ProofVerifier`,
`ExperimentRunner`, `ProseGenerator`, and `HumanReviewClient`. It defaults to `fake` with external
calls disabled. Fake adapters use local deterministic templates and validators. A provider-isolated
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
