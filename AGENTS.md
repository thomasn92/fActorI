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

Before large schema, storage, API, server, or cross-language refactors, inspect
`protocols/README.md`, `protocols/version.json`, and the generated JSON Schemas under
`protocols/jsonschema/`. Update them with `factori export-protocols`; do not hand-edit generated
schemas. Validate examples with `factori validate-protocol-examples` and use
`factori check-protocol-version` for schema version bump rules.

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
- Adapter provider metadata must remain provider-neutral where possible. Use the shared adapter
  capability descriptors and typed errors instead of scattered string whitelists or generic
  runtime exceptions.
- A gated OpenAI adapter exists only for Stage A candidate proposal. It requires the explicit
  `openai` backend, `allow_external_calls=true`, and an API key. Do not extend it to review,
  retrieval, verification, experiments, synthesis, prose, or human approval unless explicitly
  requested.
- A separately gated OpenAI reviewer adapter exists only for Stage B structural critique. It
  requires `reviewer_backend=openai`, `use_llm_reviewers=true`, external-call permission, and an API
  key. Reviewer output has no verification, scientific-approval, publication, proof, experiment,
  retrieval, or human-review authority.
- A gated OpenAlex adapter exists only for source metadata/abstract retrieval and bounded
  retrieval-adequacy inputs. It requires the explicit `openalex` retrieval backend,
  `allow_external_calls=true`, and configured credentials. Do not treat it as novelty proof,
  complete literature coverage, claim verification, or external-review readiness.
- A gated Lean proof adapter exists only for Stage C mathematical-branch proof checking through a
  local proof executable. It requires `proof_backend=lean`, `allow_external_tools=true`, and an
  explicitly configured proof executable. Do not execute Lean or any proof tool unless the user
  explicitly requests this backend and safety gate.
- A gated local synthetic experiment adapter exists only for Stage C SyntheticOnly branches. It
  requires `experiment_backend=local_synthetic`, `allow_external_tools=true`, and an explicitly
  configured runner. Do not execute experiment tools unless the user explicitly requests this
  backend and safety gate. Synthetic experiment output cannot be described as real-world empirical
  validation.
- A gated OpenAI prose adapter exists only for section-level manuscript drafting from approved prose
  contracts, claim tables, evidence maps, and narrative contracts. It requires `prose_backend=openai`,
  `allow_external_calls=true`, and an API key. Do not use it for final or polished full-paper
  generation, LaTeX export, claim creation, citation invention, or label/evidence changes.
- The complete Markdown manuscript drafting engine uses the prose adapter section by section and
  assembles a presentation draft. It must not be treated as polished prose, final paper generation,
  LaTeX export, citation resolution, evidence creation, or scientific validation.
- Fake validators are fake. Never describe their output as scientific truth or real validation.
- Prefer small deterministic functions, explicit Pydantic schemas, and existing local patterns.
- Keep changes scoped. Do not introduce orchestration frameworks by default.
- Import public schema models from `factori.schemas`. Only import from
  `factori.schemas.<submodule>` when editing schema internals or adding grouped schema definitions.
  Do not bypass the compatibility re-exports without a concrete reason.
- Prefer typed library entry points under `factori.commands` for command business logic when they
  exist. Keep Typer command functions focused on argument parsing, output formatting, and
  Typer-specific error conversion.
- Keep `factori.stage_b.run_stage_b` as the public Stage B entry point. Stage B internals are split
  into deterministic phases in `factori.stage_b_phases`; edit those phases for Stage B internals
  without changing the public result shape, artifact IDs, report layout, or ledger action sequence.
- Keep `factori.stage_c.run_stage_c` as the public Stage C entry point. Stage C internals are split
  into deterministic phases in `factori.stage_c_phases`; edit those phases for proof,
  experiment, evidence-classification, and persistence internals without changing artifact IDs,
  report layout, evidence rules, or ledger action sequence.

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
- LLM reviewer prompts, responses, parse reports, objections, and recommendations are also
  provenance/context only and must never assign verification labels.
- Retrieval queries, responses, normalized sources, documents, and adequacy certificates are
  literature context only. They are not proof, experiment, claim-verification, or human-approval
  evidence.
- Real proof contracts, payloads, traces, results, and safety reports must stay candidate/claim
  specific. Only a validated real proof result or transcript artifact with proof-evidence metadata
  can support `LeanVerified`; LLM, reviewer, retrieval, Markdown, LaTeX, and paper artifacts cannot.
- Local synthetic experiment contracts, inputs, outputs, traces, results, and safety reports must
  stay candidate/claim specific. Only a validated SyntheticOnly local experiment result with
  experiment-evidence metadata can support `SyntheticExperimentVerified`; it can never support
  `RealDataExperimentVerified` or empirical validation claims.
- Prose prompts, generated section drafts, complete Markdown manuscript drafts, drafting reports,
  assembly reports, and prose safety reports are manuscript/prose context only. They are not proof,
  experiment, retrieval, literature, human-review, or scientific-validation evidence and must never
  create or upgrade claim labels, evidence links, or citation records.
- Citation registries, citation-safety reports, bibliography placeholders, and literature-positioning
  reports are retrieval/manuscript context only. They must not be described as exhaustive literature
  coverage, novelty proof, claim verification, proof evidence, experiment evidence, human approval,
  or scientific validation.
- Replay and diagnostics must be read-only and must not create ledger commits.
- Runtime summaries, manifests, replay reports, and diagnostics reports are derived views. They must
  not replace the append-only ledger as provenance.

## Development Rules

- Do not mutate or prune existing ledger history.
- Mutating stage commands fail closed when completion artifacts already exist. Preserve the
  explicit rerun policy; do not bypass it with direct duplicate-style commits. Use
  `SkipIfComplete` for no-op resumes or `AllowIfForced` plus an explicit force request when a
  deliberate rerun is required.
- Treat ledger fork, broken-parent, and multiple-tip findings as consistency failures. Validation
  commands are read-only and must never repair or rewrite ledger history.
- Preserve atomic artifact writes: use same-directory temporary files, replace atomically, and hash
  final on-disk bytes. Do not bypass `ArtifactStore` for normal pipeline artifacts.
- Use the shared helpers in `factori.persistence` for new artifact-write plus ledger-commit paths
  when they fit. Do not hand-roll write/append/link sequences without a concrete reason.
- Use the `Clock` seam for new persistence/orchestration timestamps so tests can remain deterministic.
- Do not silently upgrade claim labels or data regimes.
- Do not treat generated presentation files as evidence.
- Narrative manuscript contracts and paper-shape critiques are manuscript-quality diagnostics only.
  They must not upgrade claim labels, override evidence rules, or be described as scientific
  validation.
- Protocol schemas and examples are developer contracts. They are not run provenance or scientific
  evidence.
- Add tests in proportion to behavior and invariant risk.
- Always run pytest and Ruff after code changes:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Equivalent `uv run pytest` and `uv run ruff check .` commands are also supported.
