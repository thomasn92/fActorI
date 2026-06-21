# Module Map

All implementation modules are under `factori/`.

## Core

- `__init__.py`: package marker and deterministic-MVP description.
- `config.py`: repository and run-layout constants.
- `schemas.py`: strict Pydantic models, enums, labels, and cross-stage contracts.
- `ledger.py`: append-only SQLite commit ledger and hash-chain validation.
- `artifacts.py`: local artifact store and artifact-to-commit metadata links.
- `hashing.py`: canonical JSON and SHA-256 helpers.
- `protocols.py`: stable language-neutral protocol names mapped to existing typed Python models.
- `schema_export.py`: deterministic JSON Schema, version metadata, and example export/check logic.
- `cli.py`: Typer command surface for all implemented stages.

## Protocol Contracts

- `protocols/README.md`: consumer guidance, alias policy, versioning, and evidence boundaries.
- `protocols/version.json`: explicit protocol version and generator metadata.
- `protocols/jsonschema/`: generated JSON Schema Draft 2020-12 contracts.
- `protocols/examples/`: deterministic cross-language payload fixtures.

## Adapters

- `adapters/base.py`: small runtime-checkable protocols for candidate LLM, structural reviewer,
  retrieval, proof, experiment, prose, and human-review backends.
- `adapters/config.py`: strict fake-default backend/model configuration with external calls
  disabled and API keys excluded from reports.
- `adapters/fake.py`: deterministic local implementations that delegate to current templates and
  fake validators without network, subprocess, Lean, Docker, or human access.
- `adapters/llm_prompts.py`: deterministic Stage A candidate prompt and structured-output contract.
- `adapters/llm_safety.py`: strict parsing, candidate validation, label-inflation checks, and MVP
  data-boundary enforcement for untrusted LLM output.
- `adapters/llm_real.py`: provider-isolated OpenAI Responses transport and injected-transport
  Stage A LLM client; no network call occurs unless explicitly enabled and invoked.
- `adapters/reviewer_prompts.py`: deterministic Stage B reviewer prompt and structured panel schema.
- `adapters/reviewer_safety.py`: score normalization and rejection of reviewer verification,
  publication, exhaustive-literature, and synthetic-to-real-world authority claims.
- `adapters/llm_review.py`: fake reviewer wrapper plus gated OpenAI Stage B structural reviewer
  using the existing injectable transport.
- `adapters/retrieval_sources.py`: deterministic OpenAlex query contracts, URL/DOI normalization,
  abstract reconstruction, and source/document provenance hashes.
- `adapters/retrieval_safety.py`: strict provider-result validation and malformed-source rejection.
- `adapters/retrieval_real.py`: provider-isolated OpenAlex transport and injected-transport client
  for source metadata, abstracts, and bounded retrieval adequacy.
- `adapters/registry.py`: fake-default registry and explicit gates for Stage A OpenAI, Stage B
  OpenAI reviewer, and Stage B OpenAlex adapters.
- `adapters/__init__.py`: public adapter interface, configuration, fake, and registry exports.

## Pipeline Orchestration

- `pipeline.py`: canonical stage order, start/stop validation, read-only stage classification,
  and deterministic overall status derivation.
- `run_all.py`: explicit direct orchestration of existing stages plus the hashed, ledgered pipeline
  run report. Replay and diagnostics are checked for ledger immutability around their execution.
- `checkpoints.py`: explicit stage completion artifacts and resume prerequisite tables.
- `status.py`: read-only run status inspection, next-stage recommendation, and resume validation.
- `pipeline_plan.py`: expected output tables and stage metadata used by dry-run planning.
- `dry_run.py`: read-only run-all planning, blocker detection, and dry-run validation.
- `run_files.py`: read-only run-directory indexing, artifact-link classification, and
  non-provenance boundary markers.
- `output_hygiene.py`: deterministic manifest/file/hash hygiene checks and optional reports that
  remain outside provenance.
- `remediation.py`: explicit finding-to-action mappings, risk classification, and deterministic
  producing-stage inference without action execution.
- `hygiene_plan.py`: read-only remediation-plan construction, summaries, and optional reports that
  remain outside provenance.

## Stage A

- `stage0.py`: fake deterministic opportunity discovery.
- `stage_a.py`: Stage 0/A orchestration, optional gated LLM candidate proposal, trace provenance,
  data gate, candidate artifact flow, scoring, deduplication, and ranking.
- `scoring.py`: deterministic fake score vectors and cost-aware scoring.
- `dedup.py`: deterministic candidate distance and duplicate decisions.

## Control

- `questioner.py`: Strategic Questioner selection and action routing.
- `autonomy.py`: deterministic HumanRequired predicate and autonomy contract.
- `stagnation.py`: global stagnation index and forced actions.
- `retrieval.py`: retrieval adequacy certificate skeleton plus stage-owned query/response/result/
  certificate artifact and ledger flow.
- `runtime_summary.py`: non-provenance runtime context compression.

## Stage B

- `stage_b.py`: Stage B child expansion, optional per-parent gated retrieval, optional gated LLM
  structural reviewers, checks, gate, ranking, artifacts, and ledger flow.
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

## Packaging, Audit, Export, Replay, Diagnostics, and Comparison

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
- `cross_run.py`: typed disk snapshots, field-level run differences, and optional comparison reports.
- `regression_diagnostics.py`: deterministic regression rules and comparison summaries.

## Reports

- `reports.py`: deterministic Markdown renderers for stage, package, audit, export, replay,
  diagnostics, cross-run comparison, pipeline run, output hygiene, and remediation-plan reports.
  These rendered reports are presentation artifacts, not verification evidence.
