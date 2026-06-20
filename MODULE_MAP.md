# Module Map

All implementation modules are under `factori/`.

## Core

- `__init__.py`: package marker and deterministic-MVP description.
- `config.py`: repository and run-layout constants.
- `schemas.py`: strict Pydantic models, enums, labels, and cross-stage contracts.
- `ledger.py`: append-only SQLite commit ledger and hash-chain validation.
- `artifacts.py`: local artifact store and artifact-to-commit metadata links.
- `hashing.py`: canonical JSON and SHA-256 helpers.
- `cli.py`: Typer command surface for all implemented stages.

## Stage A

- `stage0.py`: fake deterministic opportunity discovery.
- `stage_a.py`: Stage 0/A orchestration, data gate, candidate artifact flow, and ranking.
- `scoring.py`: deterministic fake score vectors and cost-aware scoring.
- `dedup.py`: deterministic candidate distance and duplicate decisions.

## Control

- `questioner.py`: Strategic Questioner selection and action routing.
- `autonomy.py`: deterministic HumanRequired predicate and autonomy contract.
- `stagnation.py`: global stagnation index and forced actions.
- `retrieval.py`: retrieval adequacy certificate skeleton.
- `runtime_summary.py`: non-provenance runtime context compression.

## Stage B

- `stage_b.py`: Stage B child expansion, checks, gate, ranking, artifacts, and ledger flow.
- `reviewers.py`: deterministic fake reviewer panels and disagreement resolution.
- `bridge.py`: deterministic bridge survival and one-repair policy.
- `baselines.py`: deterministic baseline validation.
- `redteam.py`: deterministic Stage B red-team and triviality checks.

## Stage C

- `stage_c_selection.py`: pre-Stage-C red-team aggregation and candidate selection.
- `uncertainty.py`: deterministic score uncertainty and conservative lower bounds.
- `budget.py`: cost-aware Stage C budget selector.
- `stage_c.py`: branch classification and fake Stage C verification orchestration.
- `proof_fake.py`: deterministic fake proof validator.
- `experiments_fake.py`: deterministic fake synthetic-experiment validator.
- `evidence.py`: claim-label/evidence admissibility boundaries.

## Synthesis and Manuscript

- `abstract_synthesis.py`: abstract-model proposals, scoring, attacks, and synthesis artifacts.
- `final_selection.py`: deterministic abstract-or-branch final nucleus selection.
- `claims.py`: claim/evidence table and claim-admissibility helpers.
- `manuscript_plan.py`: section-level manuscript planning.
- `draft_skeleton.py`: deterministic Markdown/JSON draft scaffold generation.
- `checklist.py`: manuscript checklist generation.
- `final_paper.py`: assembled paper-shaped skeleton and assembly readiness report.

## Packaging, Audit, Export, Replay, and Diagnostics

- `manifest.py`: artifact and reproducibility manifests.
- `research_object.py`: reproducible research-object packaging.
- `run_summary.py`: ledger and branch-outcome summaries.
- `final_audit.py`: deterministic internal-consistency audit orchestration.
- `release_gate.py`: release status from final-audit findings.
- `export_plan.py`: export section/claim maps, readiness, and artifact orchestration.
- `prose_contract.py`: label-preserving future prose-generation contract.
- `latex_plan.py`: safe future LaTeX-export plan without generating LaTeX.
- `replay.py`: public read-only replay API and optional non-provenance report writer.
- `run_verifier.py`: disk-based ledger, artifact, evidence, and decision consistency checks.
- `failure_explainer.py`: explicit root-cause mappings and deterministic rerun recommendations.
- `diagnostics.py`: disk-loaded read-only diagnostics and optional non-provenance report writer.

## Reports

- `reports.py`: deterministic Markdown renderers for stage, package, audit, export, replay, and
  diagnostics reports. These rendered reports are presentation artifacts, not verification
  evidence.
