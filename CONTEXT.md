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

The full design reference remains `fActori_updated_data_regime.tex` at the repository root. Use it
only when the compressed context and implementation do not answer a specification question.
