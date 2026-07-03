# Implemented Milestones

| Milestone | Scope | Main modules | Main CLI | Ledger behavior |
| --- | --- | --- | --- | --- |
| 0-2 | Foundation, schemas, SQLite ledger, artifact store, initial CLI | `schemas/`, `ledger.py`, `artifacts.py`, `hashing.py`, `config.py`, `cli.py` | `init-run`, `add-candidate`, `write-artifact`, `show-ledger`, `validate-run` | Initialization and writes mutate; inspection and validation are read-only |
| 3 | Deterministic Stage 0 opportunity discovery and Stage A generation/scoring/dedup/gate | `stage0.py`, `stage_a.py`, `scoring.py`, `dedup.py` | `run-stage-a` | Mutates ledger and writes hashed artifacts |
| 4 | Strategic Questioner, autonomy contract, stagnation, retrieval adequacy, runtime compression | `questioner.py`, `autonomy.py`, `stagnation.py`, `retrieval.py`, `runtime_summary.py` | `questioner-check`, `retrieval-adequacy-demo`, `stagnation-demo` | `questioner-check` mutates; demos and runtime compression are read-only |
| 5 | Deterministic Stage B structural validation | `stage_b.py`, `reviewers.py`, `bridge.py`, `baselines.py`, `redteam.py` | `run-stage-b` | Mutates ledger and writes hashed artifacts |
| 6 | Deterministic Stage B-to-C red-team filtering and budgeted selection | `stage_c_selection.py`, `uncertainty.py`, `budget.py` | `select-stage-c` | Mutates ledger and writes hashed artifacts |
| 7 | Deterministic Stage C fake verification and evidence boundaries | `stage_c.py`, `proof_fake.py`, `experiments_fake.py`, `evidence.py` | `run-stage-c` | Mutates ledger and writes hashed fake evidence/report artifacts |
| 8 | Deterministic abstract synthesis and final nucleus selection | `abstract_synthesis.py`, `final_selection.py` | `synthesize-abstract` | Mutates ledger and writes hashed artifacts |
| 9 | Deterministic manuscript planning and claim/evidence table | `manuscript_plan.py`, `claims.py` | `plan-manuscript` | Mutates ledger and writes hashed planning artifacts |
| 10 | Deterministic draft skeleton and manuscript checklist | `draft_skeleton.py`, `checklist.py` | `build-draft-skeleton` | Mutates ledger and writes hashed presentation/planning artifacts |
| 11 | Deterministic research object packaging, manifests, and run summaries | `research_object.py`, `manifest.py`, `run_summary.py` | `package-research-object` | Mutates ledger and writes hashed packaging artifacts |
| 12 | Deterministic final-paper assembly skeleton | `final_paper.py` | `assemble-paper-skeleton` | Mutates ledger and writes hashed presentation artifacts |
| 13 | Deterministic final audit and release gate | `final_audit.py`, `release_gate.py` | `final-audit` | Mutates ledger and writes hashed audit/release artifacts |
| 14 | Deterministic prose contract, LaTeX plan, and export readiness | `export_plan.py`, `prose_contract.py`, `latex_plan.py` | `prepare-export` | Mutates ledger and writes hashed export-planning artifacts |
| 15 | Deterministic read-only replay verification from disk | `replay.py`, `run_verifier.py` | `replay-verify` | Read-only; optional replay reports are not ledgered or added to provenance |
| 16 | Deterministic provenance diagnostics and explain-failure recommendations | `diagnostics.py`, `failure_explainer.py` | `diagnose-run` | Read-only; optional diagnostic reports are not ledgered or added to provenance |
| 17 | Read-only cross-run comparison and deterministic regression diagnostics | `cross_run.py`, `regression_diagnostics.py` | `compare-runs` | Read-only; optional comparison reports are not ledgered or added to provenance |
| 18 | Canonical deterministic one-command orchestration with stop/resume and optional read-only checks | `pipeline.py`, `run_all.py` | `run-all` | Existing mutating stages append normally; the pipeline report is hashed and ledgered; replay and diagnostics remain read-only |
| 19 | Read-only checkpoint/status inspection and stricter resume prerequisite validation | `checkpoints.py`, `status.py` | `status`, `validate-resume` | Read-only; `run-all --start-at` uses validation before any resumed mutation |
| 20 | Read-only pipeline dry-run planning for run-all options, blockers, and expected outputs | `dry_run.py`, `pipeline_plan.py` | `run-all --dry-run`, `plan-run` | Read-only; dry-run plans are stdout/JSON only and are not ledgered |
| 21 | Read-only run output hygiene inspection for manifest drift, orphaned/stale/duplicate files, and non-provenance leakage | `output_hygiene.py`, `run_files.py` | `inspect-hygiene` | Read-only; optional hygiene reports are outside provenance, evidence, manifests, and the ledger |
| 22 | Deterministic non-executing hygiene remediation planning with risk levels and producing-stage recommendations | `hygiene_plan.py`, `remediation.py` | `plan-hygiene-remediation` | Read-only; recommendations are never executed and optional plans remain outside provenance and the ledger |
| 23 | Explicit backend adapter interfaces with deterministic fake defaults and fake-only registry enforcement | `adapters/base.py`, `adapters/fake.py`, `adapters/registry.py`, `adapters/config.py` | `show-adapters`, `adapters` | Registry inspection is read-only; stage-owned adapter outputs continue through existing artifact and ledger paths |
| 24 | First real-but-gated LLM candidate-generation adapter with deterministic prompt/safety contracts and fake defaults | `adapters/llm_real.py`, `adapters/llm_prompts.py`, `adapters/llm_safety.py`, `stage_a.py` | `run-stage-a --adapter-backend openai --allow-external-calls`, `run-all` with the same opt-in | Real Stage A proposals and sanitized traces are hashed and ledgered; fake default behavior is unchanged |
| 25 | Gated OpenAlex source retrieval with deterministic normalization, source provenance, bounded adequacy, and fake defaults | `adapters/retrieval_real.py`, `adapters/retrieval_sources.py`, `adapters/retrieval_safety.py`, `retrieval.py`, `stage_b.py` | `run-stage-b --retrieval-backend openalex --allow-external-calls`, `retrieval-adequacy-demo` with the same opt-in | Real retrieval context is hashed and ledgered in Stage B; it is not verification evidence or novelty proof |
| 26 | Gated OpenAI Stage B structural reviewer with deterministic prompts, parsing, safety fallback, and fake defaults | `adapters/llm_review.py`, `adapters/reviewer_prompts.py`, `adapters/reviewer_safety.py`, `reviewers.py`, `stage_b.py` | `run-stage-b --reviewer-backend openai --use-llm-reviewers --allow-external-calls` | Reviewer traces and reports are hashed and ledgered as non-evidence context; reviewer output has no verification or publication authority |
| 26.5 | Language-neutral versioned protocol definitions, deterministic JSON Schema export, and interoperability examples | `protocols.py`, `schema_export.py`, `protocols/` | `export-protocols`, `export-protocols --check` | Read-only with respect to runs and ledgers; generated developer contracts are not provenance or evidence |
| 27 | Conservative protocol compatibility checking and deterministic schema-change classification | `protocol_compat.py`, `schema_diff.py`, `protocols/compatibility.md` | `check-protocol-compat` | Read-only developer-contract comparison; creates no files, run artifacts, or ledger commits |
| 27-P | Persistence hardening I: atomic artifact/sidecar replacement, newline-stable hashing, injectable clocks, and storage protocols | `storage_protocols.py`, `artifacts.py`, `ledger.py`, `run_all.py` | Existing commands unchanged | Existing mutating stages retain append-only behavior; persistence is safer and fixed clocks are injectable for tests |
| 28 | Persistence hardening II: explicit rerun policy, artifact-based duplicate-stage prevention, and ledger tip/fork validation | `rerun_policy.py`, `run_all.py`, `status.py`, `ledger.py` | Mutating commands with `--rerun-policy`/`--force`; `validate-ledger-tip` | Mutating reruns fail closed by default; validation is read-only and never repairs history |
| 29 | Server/protocol hardening: expanded run-control, adapter I/O, manifest, and enum schemas; JSON Schema example validation; timestamp and versioning rules | `protocols.py`, `schema_export.py`, `protocol_validation.py`, `protocol_versioning.py`, `protocols/versioning.md`, `protocols/server-readiness.md` | `export-protocols`, `validate-protocol-examples`, `check-protocol-version` | Read-only developer-contract operations; protocol files are not run provenance or evidence |
| 30 | Adapter-provider hardening: provider-neutral capability descriptors, shared typed adapter errors, shared HTTP/JSON utilities, and transport-failure tests | `adapters/capabilities.py`, `adapters/errors.py`, `adapters/http.py`, `adapters/registry.py` | `show-adapters`, existing gated adapter commands | Fake defaults unchanged; real adapter requests still fail closed without explicit gates; transport tests use injected fakes and make no network calls |
| 31 | Deterministic narrative manuscript contract and paper-shape critic for central message, problem framing, novelty positioning, one-main-result focus, numerics, empirical boundaries, and appendix allocation | `narrative_contract.py`, `paper_shape.py`, `manuscript_plan.py` | `critique-paper-shape` | Read-only by default; optional reports are hashed and ledgered manuscript-quality context, not verification evidence |
| 32 | Schema/module maintainability hardening: split the large schema monolith into a grouped `factori.schemas` package with stable compatibility re-exports | `schemas/`, `protocols.py`, `schema_export.py` | Existing commands unchanged | Refactor-only; public schema imports and protocol output remain stable |
| 33 | CLI/library boundary hardening: extract selected CLI-owned business logic into typed library entry points | `commands/candidates.py`, `commands/artifacts.py`, `commands/questioner.py`, `commands/retrieval_demo.py`, `cli.py` | `add-candidate`, `write-artifact`, `questioner-check`, `retrieval-adequacy-demo` | Existing mutability behavior preserved; CLI output remains compatible |
| 34 | Stage B internal phase refactor: split `run_stage_b` into deterministic internal phases while preserving the public entry point | `stage_b.py`, `stage_b_phases.py` | `run-stage-b` | Refactor-only; Stage B artifacts, report layout, scoring, gates, and ledger actions remain compatible |
| 35 | Shared artifact persistence helpers for Stage A and Stage B output artifacts and producing commits | `persistence.py`, `stage_a.py`, `stage0.py`, `retrieval.py`, `stage_b_phases.py` | Existing commands unchanged | Refactor-only; artifact IDs, paths, reports, ledger action types, and protocol output remain compatible |
| 36 | Gated local proof-verification adapter with fake defaults, strict proof contracts, and proof-evidence safety checks | `adapters/proof_real.py`, `adapters/proof_contracts.py`, `adapters/proof_safety.py`, `stage_c.py`, `evidence.py` | `run-stage-c --proof-backend lean --allow-external-tools --proof-executable <tool>` | Fake default behavior unchanged; real proof artifacts are hashed and ledgered only after explicit local-tool opt-in |
| 37 | Gated local synthetic experiment runner with fake defaults, strict experiment contracts, and synthetic-evidence safety checks | `adapters/experiment_real.py`, `adapters/experiment_contracts.py`, `adapters/experiment_safety.py`, `stage_c.py`, `evidence.py` | `run-stage-c --experiment-backend local_synthetic --allow-external-tools --experiment-runner <tool>` | Fake default behavior unchanged; local synthetic artifacts are hashed and ledgered only after explicit local-tool opt-in and cannot support real-data validation |
| 38 | Stage C maintainability hardening: split proof, experiment, evidence-classification, summary, and persistence responsibilities into deterministic internal phases | `stage_c.py`, `stage_c_phases.py` | Existing `run-stage-c` command unchanged | Refactor-only; Stage C artifacts, evidence boundaries, report layout, ledger actions, and protocol output remain compatible |
| 39 | Gated one-section LLM prose-generation adapter with fake defaults and strict claim/evidence grounding | `adapters/prose_real.py`, `adapters/prose_prompts.py`, `adapters/prose_safety.py`, `prose_contract.py` | `generate-section-draft` | Fake default behavior unchanged; optional prose artifacts are hashed and ledgered manuscript/prose context only, not verification evidence |
| 40 | Section-by-section Markdown manuscript drafting with fake-default prose, strict safety validation, and complete draft assembly | `manuscript_drafting.py`, `manuscript_assembly.py`, `prose_contract.py`, `adapters/prose_safety.py` | `draft-manuscript` | Optional draft artifacts are hashed and ledgered manuscript/prose/presentation context only, not verification evidence |
| 41 | Citation registry and bounded literature-positioning integration for Markdown manuscript drafts | `citations.py`, `literature_positioning.py`, `manuscript_drafting.py`, `manuscript_assembly.py`, `adapters/prose_safety.py` | `build-citation-registry`, `draft-manuscript --include-citations` | Optional citation/literature artifacts are hashed and ledgered manuscript/context artifacts only; citations are not proof, experiment, human approval, scientific validation, or novelty proof |
| 42 | LaTeX export, bibliography placeholders, source-map preservation, safety checks, and optional gated render diagnostics from complete Markdown drafts | `latex_export.py`, `latex_safety.py`, `latex_render.py`, `manuscript_assembly.py`, `citations.py` | `export-latex` | Optional LaTeX/export artifacts are hashed and ledgered presentation/export context only; render checks are gated and never imply scientific validation or publication readiness |
| 43 | Paper critic and deterministic safe fake revision loop over Markdown/LaTeX artifacts | `paper_critic.py`, `paper_revision.py`, `paper_shape.py`, `latex_safety.py` | `critique-paper`, `revise-paper` | Critique is read-only by default; optional revision artifacts are hashed and ledgered manuscript/revision context only and cannot create evidence, labels, citations, or publication readiness |
| 44 | End-to-end full-paper generation command chaining citation registry, manuscript drafting, LaTeX export, paper critique, and optional safe fake revision/re-export | `full_paper_generation.py`, `citations.py`, `manuscript_drafting.py`, `latex_export.py`, `paper_critic.py`, `paper_revision.py` | `generate-paper` | Mutating orchestration over presentation/context artifacts; revision and render checks are gated and generated paper packages cannot create evidence, labels, citations, or publication readiness |
| 45 | Deterministic full-paper bundle release/readiness gate for human-review readiness, artifact completeness, citation/LaTeX safety, critic thresholds, evidence boundaries, and provenance consistency | `full_paper_release.py`, `paper_critic.py`, `citations.py`, `latex_safety.py`, `evidence.py` | `evaluate-paper-release` | Read-only by default; optional readiness reports are hashed and ledgered audit/context artifacts that cannot create evidence or imply publication readiness |
| 46 | Deterministic end-to-end paper-generation golden fixture covering full pipeline, safe fake revision, LaTeX re-export, human-review readiness, replay, hygiene, audit, rerun safety, and protocol/example stability | `tests/test_end_to_end_paper_generation_golden.py`, `protocols/examples/full-paper-golden-bundle.example.json` | Documented golden smoke sequence | Regression-only; creates no new runtime behavior, adapters, evidence authority, or publication-readiness claim |
| 47 | Explicit gated end-to-end LLM-assisted paper orchestration with fake smoke mode, real-backend gates, budget/rate metadata, call accounting, paper generation, and release evaluation | `llm_orchestration.py`, `llm_budget.py`, `run_all.py`, `full_paper_generation.py`, `full_paper_release.py` | `run-llm-paper` | Real LLM mode fails closed without external-call permission, credentials, and explicit budget; orchestration/budget/accounting reports are non-evidence audit/context artifacts |
| 48a | OpenAI live-smoke diagnostics and model-flag hardening for gated LLM orchestration | `adapters/http.py`, `adapters/errors.py`, `adapters/llm_real.py`, `adapters/llm_review.py`, `adapters/prose_real.py`, `llm_orchestration.py`, `cli.py` | `run-llm-paper --preflight-only`, `run-llm-paper --candidate-model ... --reviewer-model ... --prose-model ...` | OpenAI 4xx/5xx failures now preserve sanitized truncated response bodies and request/model hashes; preflight validates gates without network or run mutation |
| 48b | OpenAI strict structured-output JSON Schema compatibility for candidate, reviewer, and prose transports | `adapters/openai_schema.py`, `adapters/llm_real.py` | Existing gated OpenAI commands | OpenAI transport schemas are adapter-local strict copies with every property required and optional values nullable; public protocol schemas and fake defaults remain unchanged |
| 48c | Live-smoke stage isolation and hard runtime LLM budget enforcement | `llm_orchestration.py`, `llm_budget.py`, `cli.py` | `run-llm-paper --llm-scope candidate-only`, `run-llm-paper --llm-scope full-paper` | Candidate-only live smoke runs isolated Stage A candidate generation only; runtime budget guards block over-limit LLM transport attempts before external calls and record non-evidence `Blocked` accounting records |
| 48d | Reviewer-only live-smoke isolation and structural Stage B LLM call planning | `llm_orchestration.py`, `stage_b_phases.py`, `cli.py` | `run-llm-paper --llm-scope reviewer-only` | Reviewer-only runs Stage A and Stage B only; preflight plans one review call per deterministic Stage B child, and full-paper skips downstream generation after runtime LLM budget failure |
| 48e | Full-paper prose-call planning and clean runtime prose-budget handling | `llm_orchestration.py`, `manuscript_plan.py` | Existing `run-llm-paper --llm-scope full-paper` | Preflight counts every deterministic manuscript section task; runtime prose budget exhaustion returns a blocked orchestration report with a non-external blocked accounting record instead of a traceback |
| 48f | Bounded deterministic safe repair for generated-paper textual boundary violations and post-repair warning separation | `paper_revision.py`, `full_paper_generation.py`, `llm_orchestration.py` | `run-llm-paper --enable-safe-repair` | Optional one-pass repair removes or downgrades explicit unsafe text, writes a hashed non-evidence audit report with pre/repaired/post warning buckets, re-exports revised LaTeX, and reruns critic/release checks without inventing citations or evidence |
| 49 | Compact read-only LLM run inspection summary for persisted orchestration reports | `llm_orchestration.py`, `cli.py` | `inspect-llm-run` | Reads existing LLM reports only; summarizes status, calls, budget blocks, warnings, safe-repair presence, and paper paths without mutating runs or creating evidence |
| 50 | Compact read-only generated paper bundle inspection summary | `full_paper_generation.py`, `cli.py` | `inspect-paper-bundle` | Reads existing Markdown/LaTeX/report artifacts only; prefers revised artifacts and summarizes sections, words, citations, warnings, blockers, safe-repair presence, and paths without mutating runs or creating evidence |
| 51 | Deterministic read-only generated paper bundle quality lint | `full_paper_generation.py`, `cli.py` | `lint-paper-bundle` | Reads existing preferred Markdown draft artifacts only; reports draft-quality failures/warnings separately from release readiness and never mutates runs, creates evidence, or implies publication readiness |
| 52 | Quality-aware manuscript planning and drafting constraints for less placeholder-like generated bundles | `manuscript_plan.py`, `manuscript_assembly.py`, `prose_contract.py` | `generate-paper`, `lint-paper-bundle` | Full-paper generation now uses a compact 7-section quality-aware plan, deterministic non-placeholder titles, no-evidence section pruning, and prose length/boundary guidance without changing release gates or evidence authority |
| 52b | Semantic paper-quality gates for paper-shaped drafts | `full_paper_generation.py`, `manuscript_plan.py`, `manuscript_assembly.py`, `prose_contract.py`, `adapters/prose_prompts.py` | `lint-paper-bundle`, `generate-paper` | Quality lint now gates primarily on problem framing, one bounded central contribution, method summary, evidence boundaries, limitations, provenance, title quality, heading discipline, and fake-evidence absence; word count is only a skeletal-draft warning and release/safety status remains separate |
| 52c | Safe non-evidential prose retention for paper-shaped drafts | `adapters/prose_safety.py`, `prose_contract.py`, `manuscript_drafting.py`, `manuscript_assembly.py` | `generate-paper`, `lint-paper-bundle` | Prose contracts now declare allowed non-evidence statement classes; safety retains scaffold sentences, removes unsafe sentences, inserts deterministic fallback text for required omitted sections, and reports salvage counts without permitting fake citations, proof labels, empirical validation, or publication readiness |
| 52d | Section consolidation and main-body quality accounting | `full_paper_generation.py`, `paper_revision.py`, `paper_critic.py`, `manuscript_assembly.py`, `cli.py` | `inspect-paper-bundle`, `lint-paper-bundle`, safe revision paths | Quality diagnostics separate planned main-body sections, appendices, and repair metadata; central-message repair is consolidated into planned prose, conclusion fallback is bounded and non-evidential, and appendices or word-count proxies alone do not cause fragmentation failure |
| 53 | Bounded retrieval-backed citation registry and citation-aware drafting policy | `retrieval.py`, `citations.py`, `prose_contract.py`, `manuscript_drafting.py`, `full_paper_generation.py`, `llm_orchestration.py` | `run-llm-paper --enable-retrieval --retrieval-backend fake --citation-policy registry-only`, `inspect-paper-bundle`, `lint-paper-bundle` | Explicit bounded retrieval writes provenance artifacts before drafting; only registry keys may be cited, fixture sources are visibly synthetic, bibliography output is registry-derived, and citations cannot create evidence, novelty validation, or publication readiness |
| 54 | Claim-to-source support mapping and citation placement discipline | `citations.py`, `prose_contract.py`, `manuscript_assembly.py`, `full_paper_generation.py`, `cli.py` | `generate-paper`, `inspect-paper-bundle`, `lint-paper-bundle` | Generated paper bundles now include a non-evidence `claim-support-audit.json` that classifies manuscript sentences, enforces local citation placement for source-context claims, records source-scope mismatches, and keeps citations from supporting proof, experiment, novelty, validation, or publication-readiness claims |
| 54b | Bounded semantic adjudication for claim-support audits | `claim_adjudication.py`, `citations.py`, `llm_budget.py`, `llm_orchestration.py`, `full_paper_release.py` | `run-llm-paper --claim-adjudicator-backend fake\|openai` | A fake or explicitly gated OpenAI adjudicator resolves sentence meaning for ambiguous claim language, including negated proof/validation statements, while deterministic code continues to verify registry keys, source scope, bibliography provenance, evidence artifacts, and publication-readiness boundaries |
| 54c | Citation-requirement semantics after LLM adjudication | `claim_adjudication.py`, `citations.py`, `full_paper_generation.py` | Existing claim adjudication and paper lint commands | Claim-support audits now require local registry citations only for positive external/source/literature claims; current-run status, missing retrieval support, scaffold role, retrieval limitations, and evidence-boundary statements are reported as no-citation-required while real uncited external claims still fail |
| 55 | Bounded local-source retrieval quality and relevance filtering | `retrieval.py`, `citations.py`, `llm_orchestration.py`, `full_paper_generation.py` | `run-llm-paper --enable-retrieval --retrieval-backend local --retrieval-local-path <sources.json>` | Local source metadata is scored for deterministic relevance, metadata completeness, duplicates, and registry eligibility; rejected sources remain audit context but cannot be cited, and accepted sources remain bounded background context only |
| 70 | Stable autonomous-gap fingerprints, planned-spec de-duplication, and attempt-aware loop stopping | `gap_attempts.py`, `autonomous_evidence_plan.py`, `autonomous_plan_execution.py`, `planned_spec_execution.py`, `autonomous_loop.py` | `run-autonomous-loop --max-attempts-per-gap N`, `inspect-gap-attempt-history`, `inspect-planned-spec-dedup` | Append-only derived history records stable gap/spec attempts, duplicate specs are reused or skipped, exhausted no-progress gaps stop being automation-ready, and loops stop with explicit deferred/no-progress status without hiding unresolved gaps or creating evidence |
| 71 | Attempt-aware deterministic strategy diversification for exhausted autonomous gaps | `gap_strategy_diversification.py`, `gap_attempts.py`, `autonomous_evidence_plan.py`, `autonomous_plan_execution.py`, `autonomous_loop.py` | `diversify-gap-strategies`, `inspect-gap-strategy-diversification`, `run-autonomous-loop --enable-strategy-diversification` | Exhausted proof, experiment, retrieval, and claim-revision gaps receive bounded local-only alternative strategies keyed by stable fingerprints; one novel strategy per gap enters the normal planner/executor path before final append-only deferral, without creating evidence or publication readiness |
| 72 | Gated uv-based local Python experiment sandbox | `python_experiment_sandbox.py`, `planned_spec_execution.py`, `evidence_artifact_intake.py`, `full_paper_generation.py` | `run-python-experiment-sandbox`, `inspect-python-experiment-sandbox`, `execute-planned-specs --python-sandbox-backend uv_local` | Approved local bundles run through a fixed offline uv command with dependency allowlists, AST policy checks, fixed seeds, timeouts, resource limits, logs, metrics, manifests, and hashes; only completed intake-validated artifacts support mapped bounded experiment claims |
| 73 | Autonomous experiment-template routing and loop sandbox budgets | `experiment_template_routing.py`, `autonomous_loop.py`, `planned_spec_execution.py`, `python_experiment_sandbox.py`, `full_paper_generation.py` | `route-experiment-gaps`, `inspect-experiment-gap-routing`, `run-autonomous-loop --enable-experiment-routing --python-sandbox-backend uv_local` | Unsupported empirical/result gaps can be routed to approved local experiment templates and sandbox-compatible specs; autonomous loops enforce per-loop/per-iteration sandbox budgets, keep routing and budget reports append-only, and never treat template selection or failed runs as evidence |

## Current Boundary

Milestones through 73 implement a deterministic scaffold plus explicitly gated external seams for
Stage A candidate proposal, Stage B source metadata retrieval, Stage B structural review, and
Stage C local proof checking, Stage C controlled local synthetic experiment execution, and
manuscript prose drafting. The `run-llm-paper` command can combine the existing OpenAI
candidate/reviewer/prose seams with full-paper generation and human-review readiness evaluation
only when external calls, credentials, and explicit budgets are configured. They do not implement autonomous
real agents, complete scientific literature coverage, Docker experiments, LLM
synthesis, polished full-paper writing, hard PDF-generation dependencies, publication-ready LaTeX,
or production orchestration frameworks. Stage B LLM
reviews are critiques only and are not scientific validation. Narrative paper-shape critiques are
also diagnostics only and cannot validate claims. The adapter registry is now
provider-neutral enough to add future backends behind the same fail-closed gates. The protocol layer is broad enough
for future server/Rust boundary design, but no server or Rust implementation exists yet. Schema
definitions are now grouped for maintainability while `from factori.schemas import X` remains the
public compatibility path. A small subset of CLI commands now call typed library entry points, but
the Typer command surface remains compatible. Stage B internals are phase-split for maintainability
while `run_stage_b` remains the stable public API. Stage A and Stage B artifact persistence now
share helpers for the normal write-artifact, append-commit, and producing-commit link sequence
without changing stage-specific decisions or public outputs. Stage C internals are also phase-split
for proof, experiment, evidence-classification, and persistence maintainability while
`run_stage_c` remains the stable public API. The Lean proof adapter is local,
disabled by default, runner-injected in tests, and has no authority unless proof contracts, tool
results, trace artifacts, and safety checks all pass. The local synthetic experiment adapter is
also disabled by default, runner-injected in tests, and can support only SyntheticOnly claims when
contracts, metrics, output/trace artifacts, and safety checks all pass.
The prose adapter is disabled by default, transport-injected in tests, drafts one section from
approved contracts only, and the manuscript drafting engine uses it section by section to assemble
a complete Markdown presentation draft. Citation registries and literature positioning can be added
from retrieval metadata, but they remain bounded context and cannot create claims, evidence,
scientific validation, novelty proof, or label upgrades. Complete Markdown drafts can now be
exported to LaTeX source, bibliography placeholders, source maps, and optional gated render
diagnostics, but those artifacts remain presentation/export context only.
Generated paper critiques and safe fake revision passes can identify and downgrade unsafe
manuscript language, add missing limitations or source-map warnings, and preserve known citations,
but they cannot create evidence, mutate claim/evidence tables, invent bibliography entries, upgrade
labels, or imply publication readiness.
The `generate-paper` command now chains the existing manuscript-context workflow into one
full-paper package: citation registry/literature positioning, complete Markdown draft, LaTeX
export/source map, critic report, and optional safe fake revision and re-export. This orchestration
still does not create scientific evidence, label upgrades, citations beyond the registry, or
publication readiness.
The `evaluate-paper-release` command validates generated paper bundles for internal human-review
handoff across required artifacts, hashes, ledger links, citation and LaTeX safety, current critic
findings, revision status, appendices, and evidence-boundary language. It is not peer review,
scientific validation, acceptance, or publication readiness.
The golden paper-generation fixture now pins the stable 24-artifact paper bundle, final mutating
ledger-action suffix, human-review readiness status, replay result, clean hygiene result, audit
compatibility, and protocol/example counts without asserting brittle full manuscript text.
The LLM orchestration live-smoke path now exposes separate candidate/reviewer/prose model flags,
read-only `--preflight-only`, and secret-safe OpenAI transport diagnostics with sanitized error-body
excerpts and request hashes for actionable 4xx/5xx debugging.
OpenAI strict structured-output compatibility is handled as an adapter-local transport conversion:
all object properties are required in the API schema, optional values become nullable, and public
fActorI protocol schemas remain stable.
Live-smoke scope isolation now supports `candidate-only`, `reviewer-only`, and `full-paper`;
candidate-only runs Stage A, while reviewer-only runs Stage A and Stage B without downstream
paper/release work. Stage B preflight planning counts one reviewer request per deterministic child.
Runtime LLM budget guards authorize each real transport call before execution, so over-limit
attempts are blocked before any network call and recorded as non-evidence `Blocked` accounting
records.
Generated paper bundle inspection now has two read-only views: `inspect-paper-bundle` for compact
artifact structure and `lint-paper-bundle` for deterministic draft-quality diagnostics such as
short manuscripts, placeholder titles, missing citations, and missing appendices. Neither view
changes release/safety status, evidence labels, or publication-readiness flags.
Full-paper generation now uses quality-aware manuscript planning by default: generated bundles use
deterministic non-placeholder titles where possible, a smaller 7-section paper-shaped outline,
section-level prose guidance, no empirical-results section without experiment evidence, and no
bibliography section when no retrieval-backed citation sources exist. Semantic quality lint treats
word count and section length as skeletal-draft warnings, while failures focus on missing problem
framing, missing central contribution, missing method/evidence-boundary/limitation/provenance
content, placeholder titles, heading fragmentation, fake citations, fake empirical claims, or
unsupported uncited external facts. These constraints improve draft usefulness only; they do not
weaken release/safety gates, create citations, or imply publication readiness.
Generated prose safety now separates scientific claims from manuscript scaffolding: safe
non-evidential problem framing, method description, evidence-boundary statements, limitations,
demonstration status, citation-status notes, and provenance can be retained under explicit
contract classes. Unsafe sentences are removed and audited at sentence level, and required sections
with no retained safe text receive deterministic non-evidence fallbacks. This retention path still
rejects or removes fake citations, theorem/conjecture/proof labels, empirical validation claims,
novelty-proof language, and publication-readiness claims.
Paper bundle inspection and linting now classify planned main-body sections separately from
appendices and repair metadata. Required appendices no longer count as main-body fragmentation,
safe repair demotes or merges standalone central-message headings, and the deterministic conclusion
fallback closes with bounded contribution, human-review status, and future evidence-producing
steps. These are manuscript-quality diagnostics only and do not change release or safety status.
Bounded local-source retrieval now accepts explicit JSON source metadata, writes a
`retrieval-quality-report.json`, rejects duplicate, low-relevance, or metadata-incomplete records,
and builds citation registries only from accepted records. Source relevance and quality scores are
literature-context diagnostics only; they do not prove correctness, novelty, validation,
exhaustive coverage, or publication readiness.

Milestone 74 adds opt-in bounded empirical demonstration gap creation for autonomous runs. When
enabled, a safe synthetic/local demonstration claim is added to the claim-evidence planning surface,
classified as needing a Python experiment, routed to the approved `synthetic_calibration_v1`
template, and eligible for uv-local sandbox execution within existing budgets. Completed artifacts
remain scoped experiment evidence only for the mapped bounded claim and never imply broad
validation, correctness, novelty, or publication readiness.

Milestone 75 adds a deterministic mixed-state terminal policy for the autonomous loop. It
classifies resolved, deferred, exhausted, duplicate-only, noncritical, and blocking gaps; computes
effective readiness from attempt history, strategy exhaustion, de-duplication, and sandbox budgets;
and applies newly selected deterministic strategies without an extra iteration of scheduling lag.
Clean supported/deferred runs stop without a max-iteration fallback while deferred work remains
visible and `publication_ready` remains false.

Milestone 79 adds independent read-only final bundle verification and replay-by-inspection. The
verifier checks locked hashes, physical and manifest inventories, required artifacts,
accepted-only references and LaTeX citations, scoped claim evidence, release authority,
reproducibility/environment metadata, and bundled ledger-tip consistency using only bundle
contents after optional run-id lookup. It never repairs, regenerates, or executes the bundle and
cannot create evidence or publication readiness.

Milestone 80 adds the fail-closed `run-autonomous-paper` controller. It chains deterministic base
generation, the autonomous loop, final manuscript regeneration, final release-bundle assembly,
independent read-only verification, and a bounded handoff decision in one command. Every stage is
recorded, prior run artifacts are never overwritten, safety failures block downstream handoff, and
`publication_ready` remains false.

Milestone 81 adds crash-safe controller resume. Numbered immutable checkpoints lock each stage's
artifacts, hashes, protocol version, safety status, and ledger ancestor. `--resume-existing`
verifies every completed stage before reuse, resumes from the first absent safe stage, always reruns
final bundle verification and handoff, and writes append-only controller and resume reports.
Corrupt, stale, missing, or authority-claiming checkpoints fail closed.
