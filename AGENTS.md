# Instructions for Coding Agents

## Start Here

This repository implements the deterministic MVP scaffold of fActorI. Read these compressed
project-memory files before opening the large LaTeX specification:

1. `CONTEXT.md`
2. `ARCHITECTURE.md`
3. `MILESTONES.md`
4. `MODULE_MAP.md`
5. `COMMANDS.md`

`fActori_updated_data_regime.tex` is reference material only. Read it only when a task requires
details that are not captured by the context files.

## Scope

- Keep the implementation deterministic unless a user explicitly changes that requirement.
- Do not add LLM calls unless explicitly requested.
- Do not add real literature retrieval unless explicitly requested.
- Do not add real Lean integration unless explicitly requested.
- Do not add real Docker experiment execution unless explicitly requested.
- Do not add FastAPI or a frontend unless explicitly requested.
- Adapter interfaces default to deterministic fake implementations. Do not enable or implement
  real adapters, external calls, credentials, network access, subprocesses, Docker, or Lean unless
  the user explicitly requests that backend and its safety gate.
- A gated OpenAI adapter exists only for Stage A candidate proposal. It requires the explicit
  `openai` backend, `allow_external_calls=true`, and an API key. Do not extend it to review,
  retrieval, verification, experiments, synthesis, prose, or human approval unless explicitly
  requested.
- Fake validators are fake. Never describe their output as scientific truth or real validation.
- Prefer small deterministic functions, explicit Pydantic schemas, and existing local patterns.
- Keep changes scoped. Do not introduce orchestration frameworks by default.

## Evidence and Provenance

- Preserve evidence boundaries and verification labels.
- Markdown, LaTeX, paper skeletons, export plans, replay reports, and diagnostics reports are not
  verification evidence.
- `LeanVerified` requires linked proof evidence.
- `SyntheticExperimentVerified` requires linked synthetic-experiment evidence and supports only
  synthetic or simulation claims.
- `RealDataExperimentVerified` must not be produced by the current MVP.
- Conjectures, negative results, limitations, and unsupported claims must retain their labels.
- Every mutating pipeline stage must create append-only ledger commits and content-hashed artifacts.
- Adapter outputs that affect a run must pass through existing artifact and ledger mechanisms;
  adapters must not write around provenance or bypass evidence checks.
- LLM prompts, raw responses, parse reports, and proposed candidates are provenance/context only;
  they are not proof, experiment, literature, or human-review evidence.
- Replay and diagnostics must be read-only and must not create ledger commits.
- Runtime summaries, manifests, replay reports, and diagnostics reports are derived views. They must
  not replace the append-only ledger as provenance.

## Development Rules

- Do not mutate or prune existing ledger history.
- Do not silently upgrade claim labels or data regimes.
- Do not treat generated presentation files as evidence.
- Add tests in proportion to behavior and invariant risk.
- Always run pytest and Ruff after code changes:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Equivalent `uv run pytest` and `uv run ruff check .` commands are also supported.
