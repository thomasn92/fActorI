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

## Persistence Boundary

Artifact and sidecar writes use UTF-8 with normalized LF newlines. Each write is flushed and synced
to a temporary file in the destination directory, atomically installed with `os.replace`, then
hashed from the final on-disk bytes. Pre-replace failures leave an existing final file unchanged and
remove the temporary file when possible.

`Clock`, `LedgerProtocol`, and `ArtifactStoreProtocol` define the small persistence surface used by
the current pipeline. `SystemClock` preserves normal UTC behavior. `FixedClock` can drive ledger and
pipeline-report timestamps in tests without changing stage logic or CLI semantics. These protocols
are implementation seams; they do not replace the append-only ledger as provenance.

## Language-Neutral Protocol Boundary

Stable public protocol names are registered in `factori/protocols.py` and exported from existing
Pydantic models by `factori/schema_export.py`. Checked-in JSON Schema Draft 2020-12 files live under
`protocols/jsonschema/`, with explicit version metadata and deterministic examples. Internal Python
class names may differ from stable protocol names; each schema records its source model.

Protocol export is a developer operation, not a pipeline stage. It does not inspect or mutate run
directories, append ledger commits, update artifact manifests, or create scientific evidence. A
future Rust tool or server should pin the protocol version and validate messages at process
boundaries while continuing to treat the ledger as the provenance source of truth.

## Adapter Boundary

The pipeline exposes small interfaces for candidate LLM, structural reviewer, retrieval, proof,
experiment, prose, and human-review backends. The active registry defaults to deterministic fake adapters and
`allow_external_calls=false`. One provider-isolated OpenAI adapter is available only for Stage A
candidate proposal. It cannot be selected without the `openai` backend, explicit external-call
permission, and an API key. All retrieval, proof, experiment, prose, and human-review adapters
remain fake except for one gated OpenAlex source-metadata adapter used by Stage B and one gated
OpenAI Stage B structural-review adapter. Both Stage B adapters require explicit external-call
permission and configured credentials. No subprocess, Lean,
Docker runner, or human-review service is implemented.

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
- LLM requests, responses, parse reports, and candidate proposals.
- LLM reviewer prompts, responses, parse reports, objections, and recommendations.
- retrieval queries, raw responses, normalized source records, fetched metadata, and adequacy
  certificates.
- generated protocol schemas, protocol metadata, and interoperability examples.

Fake proof and synthetic-experiment artifacts exercise evidence-link mechanics only. They are not
real Lean results or real scientific experiments.

## Provenance Boundary

The ledger is the provenance source of truth. Runtime summaries, artifact manifests, ledger
summaries, research objects, replay reports, and diagnostics are derived representations. They may
validate or summarize ledger state but must not replace, prune, or rewrite it.

Replay checks ledger continuity, stored artifact hashes, required outputs, evidence boundaries,
and consistency among final audit, release gate, and export readiness. It certifies deterministic
internal consistency only, not scientific validity.
