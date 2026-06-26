# Module Map

All implementation modules are under `factori/`.

## Core

- `__init__.py`: package marker and deterministic-MVP description.
- `config.py`: repository and run-layout constants.
- `schemas/`: strict Pydantic models, enums, labels, and cross-stage contracts grouped by domain
  with compatibility re-exports from `factori.schemas`.
  - `schemas/base.py`: strict model base, hash regex, schema error, and JSON parsing helper.
  - `schemas/enums.py`: data regimes, statuses, labels, stage names, and diagnostic enums.
  - `schemas/artifacts.py`: artifact references, artifact manifests, and ledger commit schema.
  - `schemas/candidates.py`: constraints, candidates, scores, budgets, run state, and controller
    actions.
  - `schemas/retrieval.py`: retrieval queries, results, source provenance, and adequacy
    certificate models.
  - `schemas/control.py`: questioner/autonomy/stagnation/runtime summary models.
  - `schemas/stages.py`: Stage B reviewer/bridge/baseline/red-team and Stage C selection reports.
  - `schemas/verification.py`: verification state, fake proof/experiment results, and Stage C
    verification records.
  - `schemas/adapters.py`: adapter prompt, parse, trace, generated-section, and human-review
    contracts.
  - `schemas/manuscript.py`: synthesis, claim, manuscript, narrative, draft, and paper skeleton
    models.
  - `schemas/audit.py`: packaging, audit, release, export, replay, diagnostics, and cross-run
    models.
  - `schemas/pipeline.py`: pipeline, dry-run, status, rerun, file index, hygiene, and remediation
    models.
  - `schemas/protocol_models.py`: protocol-facing schema re-export convenience module.
- `ledger.py`: append-only SQLite commit ledger and hash-chain validation.
- `artifacts.py`: local artifact store and artifact-to-commit metadata links.
- `persistence.py`: shared helpers for the normal artifact write, ledger commit, and producing
  commit-link sequence used by Stage A and Stage B outputs.
- `storage_protocols.py`: runtime-checkable ledger/artifact-store/clock interfaces plus system and
  fixed clocks.
- `hashing.py`: canonical JSON and SHA-256 helpers.
- `protocols.py`: stable language-neutral protocol names mapped to existing typed Python models.
- `schema_export.py`: deterministic JSON Schema, version metadata, and example export/check logic.
- `schema_diff.py`: conservative field/type/enum/constraint JSON Schema change classification.
- `protocol_compat.py`: read-only schema-directory comparison and aggregate compatibility status.
- `protocol_validation.py`: read-only JSON-Schema-level validation for deterministic protocol
  examples without mutating protocol or run files.
- `protocol_versioning.py`: semantic protocol version-bump checks derived from compatibility
  reports.
- `cli.py`: Typer command surface for all implemented stages.

## Command Entry Points

- `commands/__init__.py`: shared command helpers for ledger paths, latest parents, and run
  initialization.
- `commands/candidates.py`: typed `add_candidate` entry point behind `factori add-candidate`.
- `commands/artifacts.py`: typed `write_artifact` entry point behind `factori write-artifact`.
- `commands/questioner.py`: typed `run_questioner_check` entry point behind
  `factori questioner-check`.
- `commands/retrieval_demo.py`: typed `run_retrieval_adequacy_demo` entry point behind
  `factori retrieval-adequacy-demo`.

## Protocol Contracts

- `protocols/README.md`: consumer guidance, alias policy, versioning, and evidence boundaries.
- `protocols/version.json`: explicit protocol version and generator metadata.
- `protocols/jsonschema/`: generated JSON Schema Draft 2020-12 contracts.
- `protocols/examples/`: deterministic cross-language payload fixtures.
- `protocols/compatibility.md`: documented breaking, non-breaking, documentation, and unknown policy.
- `protocols/versioning.md`: MAJOR/MINOR/PATCH protocol version bump rules.
- `protocols/server-readiness.md`: future server/Rust contract boundary and remaining gaps.

## Adapters

- `adapters/base.py`: small runtime-checkable protocols for candidate LLM, structural reviewer,
  retrieval, proof, experiment, prose, and human-review backends.
- `adapters/capabilities.py`: provider-neutral capability descriptors for fake, OpenAI candidate,
  OpenAI reviewer, OpenAlex retrieval, Lean proof, and local synthetic experiment backends.
- `adapters/config.py`: strict fake-default backend/model configuration with external calls
  disabled and API keys excluded from reports.
- `adapters/errors.py`: shared adapter configuration, capability, transport, parsing, and safety
  errors with secret-safe string rendering.
- `adapters/fake.py`: deterministic local implementations that delegate to current templates and
  fake validators without network, subprocess, Lean, Docker, or human access.
- `adapters/http.py`: minimal injectable HTTP/JSON request helpers used by gated real transports.
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
- `adapters/proof_contracts.py`: deterministic Stage C proof contract construction with forbidden
  token and timeout policy.
- `adapters/proof_safety.py`: local validation for proof contracts/results and strict rejection of
  non-proof evidence artifacts.
- `adapters/proof_real.py`: gated local Lean proof verifier with injected runner support; no proof
  executable is called unless explicitly configured and enabled.
- `adapters/experiment_contracts.py`: deterministic Stage C synthetic experiment contract and
  runner-input construction.
- `adapters/experiment_safety.py`: local validation for experiment contracts/results, synthetic
  data boundaries, and strict rejection of non-experiment evidence artifacts.
- `adapters/experiment_real.py`: gated local synthetic experiment runner with injected runner
  support; no experiment executable is called unless explicitly configured and enabled.
- `adapters/registry.py`: fake-default registry, provider descriptors, and explicit gates for
  Stage A OpenAI, Stage B OpenAI reviewer, Stage B OpenAlex, Stage C Lean, and Stage C local
  synthetic experiment adapters.
- `adapters/__init__.py`: public adapter interface, configuration, fake, and registry exports.

## Pipeline Orchestration

- `pipeline.py`: canonical stage order, start/stop validation, read-only stage classification,
  and deterministic overall status derivation.
- `run_all.py`: explicit direct orchestration of existing stages plus the hashed, ledgered pipeline
  run report. It accepts an optional clock; replay and diagnostics are checked for ledger
  immutability around their execution.
- `checkpoints.py`: explicit stage completion artifacts and resume prerequisite tables.
- `status.py`: read-only run status inspection, next-stage recommendation, and resume validation.
- `rerun_policy.py`: artifact-based mutating-stage rerun decisions plus read-only ledger tip,
  parent, fork, and duplicate-stage validation.
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

- `stage_b.py`: stable public Stage B entry point and `StageBResult` assembly.
- `stage_b_phases.py`: internal deterministic Stage B phases for input loading, optional
  per-parent retrieval, child expansion, per-child reviewer/bridge/baseline/red-team/triviality
  processing, gate classification, survivor selection, and report persistence.
- `reviewers.py`: deterministic fake reviewer panels and disagreement resolution.
- `bridge.py`: deterministic bridge survival and one-repair policy.
- `baselines.py`: deterministic baseline validation.
- `redteam.py`: deterministic Stage B red-team and triviality checks.

## Stage C

- `stage_c_selection.py`: pre-Stage-C red-team aggregation and candidate selection.
- `uncertainty.py`: deterministic score uncertainty and conservative lower bounds.
- `budget.py`: cost-aware Stage C budget selector.
- `stage_c.py`: branch classification, fake Stage C verification orchestration, optional gated
  Lean proof verification artifact flow for mathematical branches, and optional gated local
  synthetic experiment artifact flow for SyntheticOnly branches.
- `proof_fake.py`: deterministic fake proof validator.
- `experiments_fake.py`: deterministic fake synthetic-experiment validator.
- `evidence.py`: claim-label/evidence admissibility boundaries.

## Synthesis and Manuscript

- `abstract_synthesis.py`: abstract-model proposals, scoring, attacks, and synthesis artifacts.
- `final_selection.py`: deterministic abstract-or-branch final nucleus selection.
- `claims.py`: claim/evidence table and claim-admissibility helpers.
- `manuscript_plan.py`: section-level manuscript planning.
- `narrative_contract.py`: deterministic central-message/problem/gap/model/result narrative
  contract construction.
- `paper_shape.py`: deterministic paper-shape critique and optional manuscript-quality report
  writing.
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
  diagnostics, cross-run comparison, pipeline run, output hygiene, remediation-plan, and
  paper-shape critique reports.
  These rendered reports are presentation artifacts, not verification evidence.
