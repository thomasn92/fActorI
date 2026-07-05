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
  -> Optional citation-safe section-by-section Markdown manuscript draft
  -> Optional LaTeX export with source maps and gated render diagnostics
  -> Optional paper critique and safe fake revision pass
  -> Optional full-paper package generation over the manuscript/export workflow
  -> Optional generated-paper human-review readiness gate
  -> Optional explicitly budgeted LLM-assisted paper orchestration
  -> Read-only replay verification
```

The SQLite ledger is append-only. Artifact contents are SHA-256 hashed and linked to their
producing commits. Filesystem artifacts live below `runs/<run_id>/`.

Stage 0 opportunity discovery is now inspectable as its own context layer. It extracts
domain primitives from broad domains, scores a deterministic local library of method lenses with
easy-win and false-bridge heuristics, and emits seed constraints for later candidate generation.
These artifacts are creative-search context only and cannot create evidence, labels, validation, or
publication readiness.

Opportunity-seeded variance augmentation is the deterministic lift immediately after Stage 0. It
expands promoted seeds over distinct question, hypothesis, theory-object, baseline, failure, paper,
and verification dimensions, diagnoses duplication and method coverage, and applies selected
branches to the derived IdeaTree through append-only context reports. It does not create Stage A
survivors, verification evidence, or publication authority.

Diversity-constrained substrate promotion converts a bounded, coverage-first subset of those
variance branches into concrete ScientificSubstrates. Selection balances easy-win and scientific
interest scores, local verification feasibility, duplicate penalties, method-lens coverage, and
branch-family coverage. Promotion links the derived IdeaTree nodes to append-only substrate
artifacts but creates no experiment or proof evidence.

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

The autonomous paper controller writes a numbered immutable checkpoint after each stage. Resume
verifies checkpoint hashes, locked stage outputs, protocol version, safety state, bundle integrity,
and ledger ancestry before reuse. Missing checkpoints with existing immutable outputs, corrupt
artifacts, or invalid ledger lineage block resume. Final bundle verification and handoff always
rerun, and resume never rewrites prior controller, bundle, verification, or checkpoint artifacts.

## Language-Neutral Protocol Boundary

Stable public protocol names are registered in `factori/protocols.py` and exported from existing
Pydantic models by `factori/schema_export.py`. Checked-in JSON Schema Draft 2020-12 files live under
`protocols/jsonschema/`, with explicit version metadata and deterministic examples. Internal Python
class names may differ from stable protocol names; each schema records its source model.
The schema package preserves historical `factori.schemas.<ModelName>` source-model paths for
protocol stability even though definitions live in grouped internal modules.

Protocol version `0.15.0` exports top-level run-control, adapter I/O, manifest, output, narrative,
prose, citation/literature-positioning, manuscript drafting, LaTeX export/render,
paper-critic/revision, full-paper generation/release, LLM orchestration/budget accounting, and enum contracts. Timestamp fields such as `timestamp`, `started_at`,
`finished_at`, `created_at`, and `retrieved_at` are emitted with JSON Schema `format: date-time`.
Python-specific path and secret
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
local synthetic experiment runner for Stage C SyntheticOnly branches, plus OpenAI prose drafting
from approved manuscript contracts. OpenAlex and OpenAI require
external-call permission and configured credentials. Lean and local synthetic experiments require
`allow_external_tools=true` plus an explicit executable/runner and are never invoked by default.
Polished full-paper writing, hard PDF-generation dependencies, Docker runners, and human-review
services are not implemented.

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

Citation registries and literature-positioning reports derived from retrieval metadata are
manuscript context only. Citation markers and bibliography entries cannot create proof,
experiment, human-review, verification, publication, empirical-validation, or novelty-proof
authority.

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

The prose adapter is a section-level manuscript-drafting seam only. It consumes section-level prose
contracts, claim tables, evidence maps, and narrative contracts, then validates the generated draft
against allowed claim IDs, evidence artifact IDs, citation IDs, labels, and word limits. The
manuscript drafting engine calls that seam section by section and assembles safe outputs into a
complete Markdown presentation draft with claim/evidence and provenance appendices. Prose request,
response, draft, complete Markdown draft, drafting report, assembly report, and safety artifacts are
manuscript/prose context only. They cannot create scientific claims, invent citations, upgrade
labels, modify claim/evidence tables, or justify proof, experiment, retrieval, human-review, or
scientific-validation evidence.

When citation support is requested, section contracts also carry allowed citation keys and bounded
literature-positioning context. The citation-safety layer rejects unknown citation keys, invented
bibliography entries, exhaustive-coverage claims, and retrieval-as-novelty-proof language.
Explicit `run-llm-paper` retrieval writes bounded source metadata before drafting and changes the
effective citation policy to `registry-only`. The deterministic fake backend marks every source as
a fixture and performs no network access; fixture citations test provenance plumbing only.

The LaTeX export layer converts complete Markdown manuscript drafts into deterministic
presentation/export artifacts: `paper.tex`, bibliography placeholders, source maps, safety reports,
and optional render diagnostics. Source maps preserve links from LaTeX blocks to manuscript
sections, claim IDs, evidence artifact IDs, citation keys, and source contract hashes. Render checks
are disabled by default and require explicit external-tool permission plus a configured LaTeX
executable. LaTeX sources, source maps, bibliography placeholders, render reports, and rendered PDFs
cannot create or upgrade labels, mutate claim/evidence tables, prove publication readiness, or
justify proof, experiment, retrieval, human-review, or scientific-validation evidence.

The paper critic and revision layer inspects generated Markdown/LaTeX artifacts for paper shape,
citation safety, evidence-boundary language, source-map coverage, and appendix/limitations
presence. `critique-paper` is read-only by default. `revise-paper` plans revisions by default and
only writes artifacts with an explicit safe fake revision flag. Revision artifacts are
manuscript/revision context only: they may downgrade unsafe wording and add missing disclaimers or
placeholders, but they cannot invent citations, mutate claim/evidence tables, create evidence,
upgrade labels, or imply publication readiness.

The full-paper generation layer is orchestration over existing non-evidence manuscript operations.
It can build or reuse citation registries, literature-positioning reports, complete Markdown
drafts, LaTeX export artifacts, critic reports, and optional safe fake revision/re-export outputs.
It does not invent missing upstream content, mutate claim/evidence tables, create scientific
evidence, upgrade labels, or imply publication readiness.

The full-paper release layer is a separate manuscript-bundle gate. It rechecks artifact presence,
content hashes, ledger links, citation and LaTeX safety, current critic findings, revision status,
appendix coverage, and evidence-boundary language. `ReadyForHumanReview` is an internal handoff
status only. It does not mean accepted, scientifically validated, verified, or publication ready.

The end-to-end golden paper fixture is a structural regression contract over this workflow. It
pins artifact IDs, paths, types, non-evidence metadata, ledger action ordering, readiness status,
source-map size, replay, hygiene, audit, and protocol validation. It intentionally avoids long
manuscript text equality and cannot establish scientific correctness or publication readiness.

The LLM orchestration layer is an explicit command over existing Stage A, Stage B, prose drafting,
full-paper generation, and generated-paper release evaluation. Fake orchestration remains local.
Real orchestration requires explicit OpenAI candidate/reviewer/prose backends, external-call
permission, credentials, selected candidate/reviewer/prose models, and budget limits before any
network-capable adapter can be constructed. A read-only preflight mode validates those gates without
network calls or run mutation. OpenAI transport failures carry sanitized truncated error-body
excerpts, redacted URLs, selected model metadata, and request hashes so live-smoke 4xx/5xx failures
are diagnosable without leaking secrets.
OpenAI structured-output schemas are API-specific transport formats, not public protocol exports.
The adapter layer converts requested output schemas into OpenAI strict-compatible copies where every
object property is required and optional values are nullable. The generated fActorI protocol schemas
remain the cross-language contracts for Python/server/Rust consumers.
Live-smoke scopes isolate risk. `candidate-only` runs only Stage A candidate generation with the
configured candidate backend and forces full-paper generation, LaTeX export, critique/revision, and
release evaluation off; `full-paper` preserves the end-to-end orchestration path. A runtime budget
guard authorizes every real candidate/reviewer/prose transport call before the request is made, so
over-budget attempts are blocked with `external_call_performed=false`.
Its configuration, budget, accounting, orchestration, and safety reports are audit/context
artifacts only. They cannot create evidence, upgrade labels, imply publication readiness, or bypass
the existing stage validators and release gate.

## Mutating and Read-Only Operations

Pipeline stages from Stage A through export preparation mutate run state. Every such stage must
append ledger commits for state-changing decisions and write content-hashed artifacts.

`export-latex --write-report` is a separate mutating presentation/export command that writes
content-hashed LaTeX artifacts and a source map. It is not part of default `run-all`. Without
`--write-report`, it computes and reports export readiness without writing artifacts. Optional
render checks remain gated by `allow_external_tools=true`.

`critique-paper` is read-only unless `--write-report` is supplied. `revise-paper` is read-only in
planning mode; `--apply-safe-fake-revision --write-report` writes content-hashed
manuscript/revision context artifacts without changing claim/evidence tables or evidence labels.

`generate-paper` is a separate mutating manuscript-package orchestration command. It is not part of
default `run-all`; it writes content-hashed full-paper report/bundle artifacts only when
`--write-report` is supplied, reuses existing manuscript/export artifacts when possible, gates
revision behind `--apply-safe-fake-revision`, and gates render checks behind
`allow_external_tools=true`.

`evaluate-paper-release` is read-only by default. With `--write-report`, it writes content-hashed,
ledgered release/readiness context reports. Those reports remain non-evidence and cannot imply
publication readiness or human approval.

`run-llm-paper` is a separate mutating orchestration command. It is not part of default `run-all`.
Fake mode can exercise the workflow without external calls. Real mode fails closed unless
`allow_external_calls=true`, required credentials are present, and an explicit LLM budget permits
the run. Scope-specific effective flags and runtime budget-blocked calls are recorded in the
orchestration report. Report artifacts remain non-evidence accounting/context artifacts.

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
- rendered PDFs and LaTeX render reports;
- paper and draft skeletons;
- research-object Markdown;
- manuscript plans and checklists;
- final audit and release reports;
- export plans, prose contracts, generated section drafts, complete Markdown drafts, drafting
  reports, assembly reports, prose safety reports, LaTeX source maps, LaTeX export reports, and
  LaTeX safety reports;
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
