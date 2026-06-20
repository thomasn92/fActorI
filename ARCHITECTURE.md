# Architecture and Invariants

## High-Level Flow

```text
Constraints
  -> Stage 0 opportunity discovery when method is absent
  -> Stage A candidates, scores, deduplication, and gate
  -> Deterministic control policies
  -> Stage B localized variants and structural validation
  -> Stage B-to-C red-team, uncertainty, retrieval, data, and budget gates
  -> Stage C fake proof/method/experiment validation
  -> Abstract synthesis or best-branch selection
  -> Claim table and manuscript plan
  -> Draft skeleton and checklist
  -> Research object and manifests
  -> Paper skeleton
  -> Final audit and release gate
  -> Export contracts and maps
  -> Read-only replay verification
```

The SQLite ledger is append-only. Artifact contents are SHA-256 hashed and linked to their
producing commits. Filesystem artifacts live below `runs/<run_id>/`.

## Adapter Boundary

The pipeline exposes small interfaces for future LLM, retrieval, proof, experiment, prose, and
human-review backends. The active registry defaults to deterministic fake adapters and
`allow_external_calls=false`. No real backend, API-key handling, network call, subprocess, Lean,
Docker runner, or human-review service is implemented.

Adapters return typed values to existing stages. Any adapter output that changes run state must
still be validated, written through the artifact store, and committed through the append-only
ledger by the owning stage. An adapter cannot bypass data gates, evidence boundaries, verification
labels, or provenance rules. Fake proof and experiment adapters remain test doubles, not scientific
validation.

## Mutating and Read-Only Operations

Pipeline stages from Stage A through export preparation mutate run state. Every such stage must
append ledger commits for state-changing decisions and write content-hashed artifacts.

Inspection commands such as `show-ledger` and `validate-run` are read-only. Replay verification is
strictly read-only. `replay-verify --write-report` may write under `runs/<run_id>/replay/`, but those
reports are marked non-provenance, non-evidence, and non-ledgered. Future diagnostics must follow
the same rule unless explicitly designed as a mutating pipeline stage.

## Data Regimes

The schema recognizes four regimes:

```text
NoData
SyntheticOnly
PublicDownload
UserProvided
```

The MVP gate allows `NoData` and `SyntheticOnly`. `PublicDownload` and `UserProvided` are deferred
as real-data candidates or marked as requiring real data. The gate is applied before expensive
verification and must not be bypassed by later presentation or synthesis stages.

## Verification Labels

```text
LeanVerified
SyntheticExperimentVerified
RealDataExperimentVerified
Conjecture
NegativeResult
Limitation
Unsupported
```

The broader schema also retains `ExperimentVerified` for compatibility, but current MVP behavior
uses the explicit synthetic/real-data distinction.

Label invariants:

- `LeanVerified` requires a linked proof evidence artifact for the exact mathematical claim.
- `SyntheticExperimentVerified` requires linked synthetic-experiment evidence and supports only
  synthetic or simulation claims.
- `RealDataExperimentVerified` must not be produced in the MVP.
- A conjecture cannot be upgraded into a theorem by synthesis, planning, or export.
- Negative results remain negative or boundary findings.
- Limitations remain limitations.
- Unsupported claims are excluded from normal main-result sections.

## Evidence Boundary

Evidence-bearing artifacts must have content hashes and producing commit hashes. Presentation and
derived artifacts cannot justify verification labels.

The following never count as verification evidence:

- Markdown and LaTeX files;
- paper and draft skeletons;
- research-object Markdown;
- manuscript plans and checklists;
- final audit and release reports;
- export plans and prose contracts;
- runtime summaries and manifests;
- replay reports and diagnostics reports.

Fake proof and synthetic-experiment artifacts exercise evidence-link mechanics only. They are not
real Lean results or real scientific experiments.

## Provenance Boundary

The ledger is the provenance source of truth. Runtime summaries, artifact manifests, ledger
summaries, research objects, replay reports, and diagnostics are derived representations. They may
validate or summarize ledger state but must not replace, prune, or rewrite it.

Replay checks ledger continuity, stored artifact hashes, required outputs, evidence boundaries,
and consistency among final audit, release gate, and export readiness. It certifies deterministic
internal consistency only, not scientific validity.
