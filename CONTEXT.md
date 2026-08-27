# Project Context

## What fActorI Is

fActorI is specified as a multi-fidelity, variance-driven agentic framework for scientific
discovery. The broader design explores candidate research branches, allocates verification effort,
tracks evidence and uncertainty, and converges on a research output with explicit provenance.

This repository is not the real autonomous research system. It is a deterministic local MVP
scaffold that exercises the intended contracts, state transitions, evidence boundaries, and output
shape without calling external systems. Fake validators and template-driven logic simulate the
pipeline so its internal invariants can be tested repeatably.

## Implemented Flow

```text
Stage A candidate generation
  -> Control layer
  -> Stage B structural validation
  -> Stage C selection
  -> Stage C fake verification
  -> Abstract synthesis
  -> Manuscript planning
  -> Draft skeleton
  -> Research object packaging
  -> Paper skeleton assembly
  -> Final audit
  -> Export preparation
  -> Optional Markdown manuscript drafting
  -> Optional LaTeX export and gated render checks
  -> Optional paper critique and safe fake revision
  -> Optional full-paper package generation
  -> Optional generated-paper human-review readiness gate
  -> Optional explicitly budgeted LLM-assisted paper orchestration
  -> Replay verification
```

The implementation uses strict Pydantic schemas, an append-only SQLite ledger, SHA-256 content
hashes, a local filesystem artifact store, deterministic scoring and gates, Typer commands, pytest,
and Ruff. Mutating stages record decisions and artifact references in the ledger. Replay reads the
completed run from disk and checks consistency without changing provenance.

## What the MVP Demonstrates

- deterministic candidate generation, scoring, deduplication, gating, and ranking;
- explicit MVP data-regime handling;
- deterministic strategic-question routing and autonomy rules;
- fake reviewer, bridge, baseline, red-team, proof, and synthetic-experiment checks;
- conservative uncertainty, retrieval-adequacy, and budget gates;
- verification-label and evidence-boundary enforcement;
- deterministic abstraction, claim planning, draft scaffolding, paper-shaped assembly, and
  presentation-only Markdown manuscript drafting;
- deterministic citation-safe LaTeX export from complete Markdown drafts, with source maps and
  optional gated render diagnostics;
- deterministic paper critique and conservative fake revision over generated Markdown/LaTeX
  artifacts without evidence, label, citation, or publication-readiness authority;
- deterministic full-paper package generation over citation, manuscript drafting, LaTeX export,
  paper critique, and optional safe fake revision artifacts without evidence, label, citation, or
  publication-readiness authority;
- deterministic generated-paper bundle readiness checks for human-review handoff without peer
  review, scientific-validation, evidence, label, or publication-readiness authority;
- deterministic golden regression coverage from the full pipeline through paper generation,
  release evaluation, replay, hygiene, audit, and protocol validation;
- explicit fail-closed LLM-assisted paper orchestration over existing Stage A, Stage B, prose,
  full-paper generation, and release-gate paths, with fake mode, budget checks, call accounting,
  isolated live-smoke scopes, hard runtime budget guards before real LLM transport calls, OpenAI
  strict transport-schema conversion, and no evidence authority;
- research-object manifests, final consistency audit, release decisions, and export contracts;
- independent read-only replay of ledger and artifact integrity from disk.
- independent read-only verification of hash-locked final release bundles from bundle contents.
- fail-closed one-command autonomous finalization across generation, autonomous evidence work,
  final manuscript regeneration, release-bundle assembly, independent verification, and handoff.
- crash-safe autonomous controller resume from verified immutable stage checkpoints, with
  append-only resume reports and deterministic fault-injection coverage.
- read-only reconstruction and append-only context export of the Stage A/B/C creative search as a
  first-class IdeaTree with explicit pruning, survival, selection, and final-branch links.
- deterministic idea-space feature vectors and PCA-like diversity diagnostics over IdeaTree nodes,
  including collapsed-axis warnings and recommended scientific mutation axes.
- deterministic ScientificSubstrate generation from idea-space mutation axes, including concrete
  model equations, variables, DGP boundaries, baselines, measurable hypotheses, result schemas,
  limitations, and failure modes as context for future bounded experiments.
- deterministic substrate-specific routing from the selected distance-decay substrate to an
  approved offline uv experiment, with bounded comparison tables and negative-result retention.
- deterministic multi-substrate tournaments that route serious ScientificSubstrate alternatives to
  offline uv experiments, compare bounded synthetic-scope metrics, and select a manuscript branch
  without claiming real-world validation or publication readiness.
- deterministic tournament-driven creative mutation that preserves the bounded winner, repairs or
  hybridizes serious alternatives, injects missing idea-space axes, and writes new IdeaTree nodes
  plus ScientificSubstrate candidates as context for future experiments.
- deterministic mutation-substrate tournaments that route the prior winner plus applied mutation
  substrates to approved offline uv experiments, score raw fit, complexity, and robustness, and
  select a bounded second-generation branch without real-world validation or publication readiness.
- bounded recursive creative-search control that validates and reuses existing idea, substrate,
  tournament, and mutation artifacts, tracks score/diversity/lineage by cycle, stops on explicit
  deterministic policy, and rebuilds a verified final bundle without creating evidence authority.
- deterministic generation-dependent mutation planning that conditions fresh branches on the
  current and previous winners, losing branches, tournament metrics, missing idea-space axes, and
  prior semantic fingerprints; selected plans append IdeaTree and ScientificSubstrate context only.
- deterministic general Stage 0 opportunity discovery that extracts domain primitives from
  domain-only prompts, scores a local library of mathematical/computational method lenses with an
  easy-win heuristic and false-bridge penalty, and emits promoted seed constraints for later
  candidate-tree generation without creating evidence or publication readiness.
- deterministic opportunity-seeded variance augmentation that expands every promoted Stage 0 seed
  across mechanism, robustness, counterexample, benchmark, and representation branch families;
  coverage-first selection appends source-linked IdeaTree context without creating evidence or
  publication readiness.
- deterministic diversity-constrained promotion of selected variance branches into concrete
  ScientificSubstrates while preserving method-lens and branch-family coverage; promoted IdeaTree
  nodes carry substrate links, but promotion creates no evidence or publication readiness.
- deterministic general routing from ScientificSubstrates to bounded next-action classes, with
  fail-closed defer/reject outcomes and non-executing command hints that carry no evidence or
  publication authority.
- deterministic offline execution specifications and bounded results for synthetic experiments,
  benchmark tournaments, and applied-math reductions; unsupported route types defer explicitly,
  and no route result creates real-world validation or publication readiness.
- explicit backend-authority records and strict non-fake production-mode checks that reject
  template-authored scientific generation, heuristic scientific judgment, and fixture metrics
  while allowing deterministic infrastructure and genuine local execution/verification stages.
- curated production-eligible domain/method atlas scanning with exclusion-only deterministic
  compatibility checks, explicitly gated non-fake LLM pair ranking, and diversity-constrained
  selection; novelty and underuse remain hypotheses until retrieval evidence exists.
- retrieval-contextualized deep opportunity discovery over selected atlas pairs, using an
  explicitly gated non-fake LLM for concrete Q/H/T/E/B generation and scientific scoring;
  mocked retrieval is development-only, real retrieval remains bounded literature context, and
  novelty/underuse remain hypotheses rather than established findings.
- schema-constrained non-fake LLM variance generation over selected deep opportunities, with
  mechanism, robustness, counterexample, benchmark, representation, stronger-baseline, and
  negative-control branches; deterministic selection and IdeaTree construction preserve backend
  and retrieval provenance without authoring science or creating evidence.
- schema-constrained non-fake LLM ScientificSubstrate construction from selected variance nodes,
  including concrete model objects, notation, assumptions, baselines, bounded verification plans,
  result schemas, negative controls, failure modes, and limitations; deterministic validation and
  coverage selection do not author science or create evidence.
- schema-constrained non-fake LLM route adjudication and execution-spec planning over selected
  ScientificSubstrates, with explicit baselines, controls, metrics, failure criteria, proof or
  retrieval obligations, allowed future labels, and forbidden claims; local contract validation
  executes nothing and creates no evidence.
- schema-constrained non-fake LLM Python experiment-code generation for executable route specs,
  followed by deterministic AST safety auditing, isolated offline local execution, and metric
  extraction exclusively from successful sandbox `output.json` artifacts. Unsafe, failed, and
  negative-control-failing scripts remain blocked or inconclusive and cannot fabricate evidence.
- schema-constrained non-fake LLM hybrid evidence-package planning over selected substrates,
  allowing symbolic drafts, proof plans, numerical illustrations, executable experiments,
  benchmarks, counterexample searches, retrieval novelty-risk checks, negative controls, and
  robustness sweeps in one bounded package. Executable components reuse the safe codegen/sandbox
  path; symbolic/proof components remain draft-labeled unless checked; retrieval novelty remains a
  risk assessment, not a proof of novelty.
- schema-constrained non-fake scientific criticism and cross-package adjudication over complete
  hybrid evidence packages, with independent baseline, tautology, DGP-rigging, false-bridge,
  claim-scope, novelty-risk, coherence, and technical-soundness roles; local score aggregation and
  fail-closed primary-nucleus gates preserve all evidence boundaries and never create metrics,
  proof verification, novelty proof, real-world validation, or publication readiness.
- schema-constrained non-fake nucleus-centered manuscript planning, drafting, critic review, and
  bounded revision over an adjudicated package, with deterministic claim/artifact and citation
  bindings, execution-artifact-only metric tables, explicit unresolved obligations, and fail-closed
  rejection of unsupported proof, novelty, real-world-validation, or publication-readiness claims.
- deterministic M106 final-paper assembly and release packaging around the latest valid revised
  nucleus manuscript, with sandbox-output-only metric-table reconstruction, real-retrieval citation
  resolution, hash-checked figures and evidence artifacts, flexible appendix/open-obligation
  records, structural verification, and a self-contained hash-locked bundle. Assembly and
  verification remain local presentation/integrity work and always preserve `publication_ready=false`.

These mechanisms guarantee only deterministic internal consistency, provenance, and label
discipline. They do not establish novelty, correctness, scientific value, literature completeness,
or external review readiness.

## Out of Scope

The following are intentionally not implemented:

```text
ungated real LLM calls
unbudgeted real LLM orchestration
secret-leaking OpenAI diagnostics
ungated real retrieval
ungated real Lean
ungated real experiments
real literature coverage
polished prose generation
production PDF generation
publication-ready LaTeX generation
external review readiness
production orchestration
```

Presentation, Markdown, LaTeX, render, revision, and planning artifacts are not verification
evidence. Fake proof and experiment adapters remain deterministic test doubles and must not be
presented as real scientific validation.

## Source of Truth

The append-only ledger is the provenance source of truth. Artifact manifests, ledger summaries,
runtime summaries, audit reports, export plans, replay reports, and future diagnostics are derived
views. They help inspect a run but cannot replace or rewrite ledger history.
