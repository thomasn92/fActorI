# Implemented Milestones

| Milestone | Scope | Main modules | Main CLI | Ledger behavior |
| --- | --- | --- | --- | --- |
| 0-2 | Foundation, schemas, SQLite ledger, artifact store, initial CLI | `schemas.py`, `ledger.py`, `artifacts.py`, `hashing.py`, `config.py`, `cli.py` | `init-run`, `add-candidate`, `write-artifact`, `show-ledger`, `validate-run` | Initialization and writes mutate; inspection and validation are read-only |
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

## Current Boundary

Milestones through 26.5 implement a deterministic scaffold plus explicitly gated external seams for
Stage A candidate proposal, Stage B source metadata retrieval, and Stage B structural review. They do not implement autonomous
real agents, complete scientific literature coverage, proof checking, experiments, LLM
synthesis/writing, polished prose, final LaTeX, or production orchestration frameworks. Stage B LLM
reviews are critiques only and are not scientific validation.
