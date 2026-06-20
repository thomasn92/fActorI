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

## Current Boundary

Milestones through 16 implement a deterministic scaffold. They do not implement real agents,
scientific retrieval, proof checking, experiments, polished prose, final LaTeX, or production
orchestration.
