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

Strict Pydantic schemas are grouped by domain under `factori/schemas/` and re-exported from the
stable `factori.schemas` namespace. Public callers should continue to import from `factori.schemas`;
the submodules exist to make schema maintenance safer without changing runtime or protocol
contracts.

Selected CLI-owned operations are also exposed through typed library entry points under
`factori.commands`. The CLI remains the user-facing Typer surface, but extracted command functions
own the deterministic side effects and return typed results without printing or exiting directly.

Stage B keeps `factori.stage_b.run_stage_b` as the stable public entry point, but the implementation
is split into deterministic internal phases in `factori.stage_b_phases`. The phase split separates
input loading, optional retrieval, child expansion, per-child structural processing, gate
classification, survivor selection, and report persistence without changing Stage B artifacts,
reports, scoring, gates, or ledger actions.

Stage C keeps `factori.stage_c.run_stage_c` as the stable public entry point, but proof,
synthetic-experiment, evidence-classification, summary, and persistence responsibilities are split
into deterministic internal phases in `factori.stage_c_phases`. The phase split does not change
Stage C branch labels, proof or experiment evidence boundaries, artifact IDs, report layout, fake
defaults, gated-tool requirements, or ledger actions.

## Persistence Boundary

Artifact and sidecar writes use UTF-8 with normalized LF newlines. Each write is flushed and synced
to a temporary file in the destination directory, atomically installed with `os.replace`, then
hashed from the final on-disk bytes. Pre-replace failures leave an existing final file unchanged and
remove the temporary file when possible.

Stage-owned artifact persistence should use `factori.persistence` where practical. These helpers
centralize the normal write artifact -> append ledger commit -> link producing commit sequence while
leaving stage-specific payloads, metadata, action types, and evidence policy explicit at the call
site.

`Clock`, `LedgerProtocol`, and `ArtifactStoreProtocol` define the small persistence surface used by
the current pipeline. `SystemClock` preserves normal UTC behavior. `FixedClock` can drive ledger and
pipeline-report timestamps in tests without changing stage logic or CLI semantics. These protocols
are implementation seams; they do not replace the append-only ledger as provenance.

Mutating stage commands use an explicit rerun policy. The default `FailIfExists` policy blocks a
stage when its required completion artifacts already exist. `SkipIfComplete` performs an explicit
no-op, while `AllowIfForced` requires `--force`. Read-only stages are always rerunnable. Ledger tip
validation checks commit hashes, parent continuity, insertion-order tip extension, forks, multiple tips, and
repeated mutating-stage start markers without rewriting or repairing history. Broken lineage makes
a run inconsistent and blocks further mutation.

## Language-Neutral Protocol Boundary

Stable public protocol names are registered in `factori/protocols.py` and exported from existing
Pydantic models by `factori/schema_export.py`. Checked-in JSON Schema Draft 2020-12 files live under
`protocols/jsonschema/`, with explicit version metadata and deterministic examples. Internal Python
class names may differ from stable protocol names; each schema records its source model.
The schema package preserves historical `factori.schemas.<ModelName>` source-model paths for
protocol stability even though definitions live in grouped internal modules.

Protocol version `0.5.0` exports top-level run-control, adapter I/O, manifest, output, narrative, prose, and enum
contracts. Timestamp fields such as `timestamp`, `started_at`, `finished_at`, `created_at`, and
`retrieved_at` are emitted with JSON Schema `format: date-time`. Python-specific path and secret
annotations are normalized with explicit `x-factori-*` metadata for cross-language consumers.
Examples are validated against generated schemas by `factori validate-protocol-examples`.
Version movement is checked by `factori check-protocol-version` using the MAJOR/MINOR/PATCH rules
in `protocols/versioning.md`.

Protocol export is a developer operation, not a pipeline stage. It does not inspect or mutate run
directories, append ledger commits, update artifact manifests, or create scientific evidence. A
future Rust tool or server should pin the protocol version and validate messages at process
boundaries while continuing to treat the ledger as the provenance source of truth.

## Adapter Boundary

The pipeline exposes small interfaces for candidate LLM, structural reviewer, retrieval, proof,
experiment, prose, and human-review backends. The active registry defaults to deterministic fake adapters and
`allow_external_calls=false`. The registry exposes provider-neutral capability descriptors for
available fake and gated real backends, and validates requested capabilities fail-closed. One
provider-isolated OpenAI adapter is available only for Stage A
candidate proposal. It cannot be selected without the `openai` backend, explicit external-call
permission, and an API key. Retrieval, proof, experiment, prose, and human-review adapters remain
fake by default. The current gated real seams are OpenAlex source metadata for Stage B, OpenAI
structural review for Stage B, a local Lean proof adapter for Stage C mathematical branches, and a
local synthetic experiment runner for Stage C SyntheticOnly branches, plus one-section OpenAI prose
drafting from approved manuscript contracts. OpenAlex and OpenAI require
external-call permission and configured credentials. Lean and local synthetic experiments require
`allow_external_tools=true` plus an explicit executable/runner and are never invoked by default.
Full manuscript generation, polished prose generation, LaTeX export, Docker runners, and
human-review services are not implemented.

Adapter configuration, capability, transport, parse, and safety failures use shared typed errors.
Transport utilities accept injected fake openers for tests, map HTTP failures to
`AdapterTransportError`, map malformed JSON to `AdapterResponseParseError`, and redact credential
query parameters from error strings.

Adapters return typed values to existing stages. Any adapter output that changes run state must
still be validated, written through the artifact store, and committed through the append-only
ledger by the owning stage. An adapter cannot bypass data gates, evidence boundaries, verification
labels, or provenance rules. Fake proof and experiment adapters remain test doubles, not scientific
validation.

The real Stage A adapter uses a deterministic prompt contract and strict local parsing. Raw request,
response, and parse-report artifacts are hashed and ledgered as non-evidence context. Accepted
candidates still pass through Pydantic validation, the MVP data gate, deterministic scoring,
deduplication, and the Stage A gate. LLM output can propose ideas only and cannot confer any
verification label.

The Stage B reviewer adapter uses a reviewer-specific prompt, parser, and safety layer. It produces
up to three normalized structural reports for the existing disagreement resolver. Unsafe,
malformed, verification-claiming, publication-approving, or synthetic-to-real-world output is
rejected and replaced by deterministic rejecting fallback reports. Reviewer request, response, and
parse artifacts are ledgered context only; they carry no proof, experiment, retrieval, human-review,
publication, or scientific-validation authority.

The OpenAlex retrieval adapter searches and fetches source metadata or abstracts only. Stage B
performs one query per Stage A survivor, writes ledgered query/response/normalization/certificate
artifacts, and reuses each bounded certificate across child variants. Stage C selection reuses the
Stage B certificate and does not repeat retrieval. Source metadata hashes establish provenance;
they do not establish novelty, complete literature coverage, claim validity, or external-review
readiness.

The Lean proof adapter is a local-tool seam for Stage C mathematical branches only. Stage C writes
proof contracts, payloads, raw traces, proof results, and safety reports as content-hashed
artifacts. A `LeanVerified` label from this path requires a real proof backend, explicit external
tool permission, zero tool exit code, no forbidden proof tokens, proof payload and transcript
hashes, linked proof-evidence artifacts, and successful safety validation. LLM, reviewer,
retrieval, Markdown, LaTeX, manuscript, and paper artifacts cannot justify proof labels.

The local synthetic experiment adapter is a local-tool seam for Stage C SyntheticOnly branches only.
Stage C writes experiment contracts, inputs, outputs, raw traces, results, and safety reports as
content-hashed artifacts. A `SyntheticExperimentVerified` label from this path requires explicit
external-tool permission, `data_regime == SyntheticOnly`, zero runner exit code, metrics satisfying
declared acceptance criteria, input/output/transcript hashes, linked synthetic-experiment evidence
artifacts, and successful safety validation. Synthetic experiment artifacts cannot justify
`RealDataExperimentVerified`, empirical validation, mathematical proof labels, or real-world
claims.

The prose adapter is a one-section manuscript-drafting seam only. It consumes section-level prose
contracts, claim tables, evidence maps, and narrative contracts, then validates the generated draft
against allowed claim IDs, evidence artifact IDs, citation IDs, labels, and word limits. Prose
request, response, draft, and safety artifacts are manuscript/prose context only. They cannot create
scientific claims, invent citations, upgrade labels, modify claim/evidence tables, or justify proof,
experiment, retrieval, human-review, or scientific-validation evidence.

## Mutating and Read-Only Operations

Pipeline stages from Stage A through export preparation mutate run state. Every such stage must
append ledger commits for state-changing decisions and write content-hashed artifacts.

Inspection commands such as `show-ledger` and `validate-run` are read-only. Replay verification is
strictly read-only. `replay-verify --write-report` may write under `runs/<run_id>/replay/`, but those
reports are marked non-provenance, non-evidence, and non-ledgered. Future diagnostics must follow
the same rule unless explicitly designed as a mutating pipeline stage.

`validate-ledger-tip`, `status`, resume validation, dry-run planning, replay, diagnostics,
comparison, hygiene inspection, and remediation planning remain rerunnable and read-only.

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
- export plans, prose contracts, generated section drafts, and prose safety reports;
- runtime summaries and manifests;
- replay reports and diagnostics reports.
- LLM requests, responses, parse reports, and candidate proposals.
- LLM reviewer prompts, responses, parse reports, objections, and recommendations.
- retrieval queries, raw responses, normalized source records, fetched metadata, and adequacy
  certificates.
- generated protocol schemas, protocol metadata, and interoperability examples.
- narrative manuscript contracts and paper-shape critiques.

Fake proof and synthetic-experiment artifacts exercise evidence-link mechanics only. The gated
Lean path is the real-proof adapter seam, and the gated local synthetic runner is the controlled
synthetic experiment seam. Both remain disabled unless explicitly selected. No real empirical
scientific experiments or real-data ingestion are implemented.

## Narrative Paper Shape Boundary

The manuscript-quality layer checks whether the planned paper has a central message, problem
framing, bounded literature positioning, a simple model frame, one main result, purposeful
numerics, synthetic/empirical boundary discipline, and appendix allocation. This is diagnostic
paper-shape feedback only. It cannot upgrade verification labels, override audit/release evidence
rules, prove novelty, validate experiments, or create scientific truth.

## Provenance Boundary

The ledger is the provenance source of truth. Runtime summaries, artifact manifests, ledger
summaries, research objects, replay reports, and diagnostics are derived representations. They may
validate or summarize ledger state but must not replace, prune, or rewrite it.

Replay checks ledger continuity, stored artifact hashes, required outputs, evidence boundaries,
and consistency among final audit, release gate, and export readiness. It certifies deterministic
internal consistency only, not scientific validity.
