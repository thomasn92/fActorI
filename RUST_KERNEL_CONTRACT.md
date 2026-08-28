# Rust Research Kernel Contract

## Status

This document is the Sol-owned design baseline for the staged Rust translation of fActorI's
deterministic trust kernel. It freezes the intended trust boundary, authority rules, compatibility
requirements, and model handoff. The current Rust implementation remains a read-only compatibility
kernel and has not received evidence or mutation authority.

Protocol baseline: `0.82.0`.

The initial migration must preserve Python orchestration and use the checked-in JSON Schemas and
protocol examples as its cross-language contract. Rust must not become a second source of
scientific judgment, manuscript content, or protocol definitions.

## Decision Summary

The target is a small deterministic kernel, not a Rust rewrite of fActorI.

Rust will eventually own:

- protocol-envelope validation for kernel operations;
- artifact bytes, paths, hashes, and producing-commit links;
- append-only ledger mutation and chain verification;
- evidence-capability construction and evidence-label transitions;
- claim-to-evidence resolution and bounded admissibility decisions;
- manifest, checkpoint, dependency-graph, and replay integrity checks;
- stable machine-readable diagnostics for those operations.

Python will continue to own:

- LLM and retrieval adapters;
- opportunity discovery and branch generation;
- scientific scoring, criticism, and adjudication requests;
- experiment and proof specification authoring;
- manuscript planning, drafting, revision, LaTeX, and presentation;
- CLI orchestration outside the narrow kernel boundary.

Experiment runners and Lean remain independent domain-specific executors. Rust validates their
contracts and evidence artifacts; it does not replace them or infer scientific truth from their
existence.

## Non-Goals

The first migration must not:

- port LLM orchestration, prompts, retrieval, prose, or scientific critics;
- port `final_paper.py` or presentation assembly;
- add PyO3, a server, Docker, network access, or an orchestration framework;
- generate Rust types for all protocol schemas;
- change public artifact IDs, paths, report shapes, action ordering, or evidence labels;
- redefine protocol schemas in Rust;
- treat type safety as scientific validation;
- make `RealDataExperimentVerified` constructible;
- allow a manuscript, citation, reviewer, or LLM artifact to mint verification authority.

## Trust Boundary

```text
Untrusted or non-authoritative inputs
  Python orchestration, LLM output, retrieval context, generated code,
  manuscript text, external process output, filesystem state
                         |
                         v
Versioned JSON protocol + strict operation envelope
                         |
                         v
              Rust research kernel
  schema checks -> path/hash checks -> ledger checks -> authority checks
                         |
                         v
Typed decision, stable diagnostics, and optionally one authorized mutation
                         |
             +-----------+-----------+
             |                       |
      Python orchestration      append-only run state
```

The process boundary should initially be a standalone CLI using JSON on stdin and stdout. The
project root is supplied once as a CLI argument. Requests contain run-relative identities, never
arbitrary host paths. stdout is reserved for one response object; diagnostics go inside that
object rather than to ad hoc logs. A syntactically invalid request has no request identity, so the
prototype returns a stable `error` response using the reserved `transport-error` request ID and
`protocol.validate` operation; this exception must remain explicit until a versioned transport
error envelope is introduced.

## Authority Modes

The kernel requires two explicit modes. Mode is part of every authority-sensitive request and
response and must never be inferred from filenames or environment variables.

### DevelopmentCompatibility

This mode preserves existing deterministic fixture behavior for regression testing. Fake proof and
fake synthetic-experiment roles may reproduce legacy structural decisions, but every response must
remain marked non-production and must not claim scientific validation, human approval, novelty, or
publication readiness.

Development compatibility decisions do not produce strict evidence capabilities.

### StrictProduction

This mode accepts only non-fake evidence produced through the gated proof or local synthetic
experiment paths. A metadata role and producing-commit hash are necessary but insufficient. The
kernel must also validate the matching contract, result, safety artifacts, raw trace, hashes,
backend authority, data regime, and success conditions before constructing a capability.

`RealDataExperimentVerified` and generic `ExperimentVerified` remain unconstructible in the current
protocol regime.

## Kernel Operations

The first public operation set is deliberately small.

Read-only operations:

- `protocol.validate`: validate one selected protocol payload and reject unknown fields;
- `hash.canonical_json`: return Python-compatible canonical bytes and SHA-256;
- `artifact.verify`: read and verify path confinement, raw bytes, content hash, metadata, and the
  complete persisted ledger link beneath the configured project root;
- `ledger.verify`: verify schema, hashes, parent links, roots, tips, run ownership, and linearity;
- `evidence.classify`: classify an artifact as context, presentation, or a capability candidate;
- `claim.resolve`: decide whether specified evidence capabilities support one claim and section;
- `checkpoint.verify`: validate completion artifacts without mutating the run;
- `replay.verify_core`: run ledger, artifact, dependency, and authority checks read-only.

Mutating operations added only after read-only differential parity:

- `artifact.persist`: atomically write one bounded artifact and return its unlinked reference;
- `ledger.append`: append one non-forking commit at the current run tip;
- `artifact.link`: persist the producing-commit sidecar for artifacts created by that commit;
- `persistence.commit_bundle`: execute the authorized artifact/commit/link sequence.

There is no generic `set_label`, `approve`, `repair`, `run_command`, or `write_file` operation.

## Protocol Boundary

The Pydantic-generated schemas remain authoritative during migration. New kernel request and
response schemas must first be added as Pydantic models and exported with
`factori export-protocols`; generated JSON Schemas must not be edited manually.

Luna must derive the exact transitive schema profile, beginning with these families:

- `ArtifactRef`, `ArtifactManifest`, and manifest entries;
- `LedgerCommit`, ledger-tip validation, and branch findings;
- `VerificationLabel` and artifact/action enums;
- `Claim`, `ClaimEvidenceLink`, `ClaimTable`, and `ClaimEvidenceMap`;
- proof verification contracts and results;
- experiment run contracts and results;
- stage checkpoints and resume validation;
- replay checks, findings, and verification reports;
- final release manifest and core bundle-verification checks, only after the first cutover.

Do not import all 422 schemas. Select the smallest transitive closure required by the operations.

Every request envelope must include:

- protocol version;
- request ID;
- operation name;
- authority mode;
- typed operation payload.

Every response envelope must include:

- protocol version and kernel version;
- matching request ID and operation;
- `accepted`, `rejected`, or `error` status;
- typed result when accepted;
- ordered stable diagnostics;
- whether state was mutated;
- authority mode used.

Transport structs are not trusted domain values. Rust must validate and convert them into private
domain types before invoking authority-sensitive logic.

## Canonical Byte Contract

Compatibility is defined by bytes, not by semantic JSON equivalence.

The current Python canonical JSON behavior is:

- Pydantic values are dumped in JSON mode;
- object keys are strings and sorted recursively;
- array and tuple order is preserved;
- separators are `,` and `:` with no added whitespace;
- non-ASCII Unicode is emitted directly as UTF-8;
- string escaping follows Python `json.dumps` behavior;
- canonical JSON used for hashing has no trailing newline;
- persisted JSON artifacts append exactly one LF byte;
- text artifacts normalize CRLF and CR to LF before UTF-8 encoding;
- binary artifacts are hashed and persisted unchanged;
- SHA-256 output is lowercase hexadecimal.

Rust must implement a Python-compatible serializer for hash-bearing payloads. Substituting RFC 8785
or ordinary `serde_json::to_string` is forbidden until a deliberate protocol migration rehashes or
versions affected objects.

Boundary restrictions:

- non-finite numbers are rejected because they are not valid protocol JSON;
- duplicate object keys are rejected as ambiguous;
- non-string object keys are rejected rather than stringified;
- arbitrary-precision integers must not silently truncate;
- timestamp strings are hashed exactly as received and are not normalized during hashing;
- negative zero and floating-point exponent formatting require golden compatibility tests.

Luna must build a golden corpus covering Unicode, control characters, empty values, nested key
ordering, integer limits, representative floats, negative zero, and exponent notation before Rust
hashes can become authoritative.

## Artifact Contract

An artifact is not trusted merely because an `ArtifactRef` parses.

Required checks:

- `run_id`, artifact IDs, filename stems, and extensions cannot escape their allowed grammar;
- resolved paths remain beneath the configured root and expected run directory;
- symlink traversal and absolute paths are rejected;
- artifact type maps to its declared run subdirectory;
- raw file SHA-256 equals `content_hash`;
- lowercase 64-character SHA-256 syntax is enforced;
- evidence artifacts have a producing commit that exists in the same run;
- the producing commit contains the same artifact ID, path, type, and content hash;
- metadata cannot override presentation-type restrictions;
- Markdown, LaTeX, and PDF remain presentation even if metadata claims otherwise.

Write semantics:

- use a same-directory temporary file;
- write all bytes, flush, and fsync the file;
- atomically replace the destination;
- fsync the containing directory where supported;
- default metadata to `is_verification_evidence=false`;
- reject blind overwrites by default;
- permit replacement only with explicit rerun authorization and the expected prior hash.

Successful output bytes must remain compatible with Python. Cross-filesystem/SQLite crash atomicity
is not guaranteed by the current helper sequence. The first Rust implementation must expose this
as a known compatibility limitation; stronger journaling is a separate reviewed hardening change,
not an accidental consequence of translation.

## Ledger Contract

The current commit hash payload contains these ordered logical fields:

```text
parent_hash
run_id
candidate_id
action_type
payload
artifact_refs
timestamp
```

Canonical object-key sorting determines serialized byte order. Artifacts produced by the commit
replace `producing_commit_hash` with the exact string `<self>` while computing the hash. Stored
commit references then contain the resulting commit hash.

Authoritative append rules:

- the first commit for a run has no parent;
- every later commit extends that run's current insertion-order tip;
- parent and child belong to the same run;
- a commit cannot create a second root, fork, broken parent, or non-tip append;
- duplicate commit hashes and duplicate artifact IDs in one commit are rejected;
- pre-linked artifact references must resolve to existing same-run commits;
- updates and deletes remain blocked by SQLite triggers;
- tip check and insert occur in one transaction that prevents concurrent writers from both winning;
- insertion order remains the compatibility order for list and replay operations;
- a caller-provided timestamp or injected clock is required for deterministic tests.

The existing SQLite schema and stored JSON columns remain readable. Any table migration requires a
separate compatibility design and is outside the first translation.

## Evidence Capability Model

Raw protocol IDs must not be accepted as proof of authority. The Rust API should use private
constructors and opaque capability types conceptually equivalent to:

```rust
struct ArtifactHash([u8; 32]);
struct CommitHash([u8; 32]);
struct VerifiedArtifact { /* private fields */ }
struct LinkedArtifact { /* private fields */ }
struct ValidatedProofEvidence { /* private fields */ }
struct ValidatedSyntheticEvidence { /* private fields */ }

enum EvidenceCapability {
    Lean(ValidatedProofEvidence),
    Synthetic(ValidatedSyntheticEvidence),
}
```

No public constructor may accept an arbitrary string and produce `ValidatedProofEvidence` or
`ValidatedSyntheticEvidence`.

### `evidence.classify` Semantic Freeze

`evidence.classify` is a read-only artifact-classification operation. It does not validate a proof
or experiment bundle, construct an `EvidenceCapability`, assign a `VerificationLabel`, or decide
claim admissibility. Its purpose is to reduce a persisted artifact to one of three classes after
integrity and provenance checks:

```rust
enum ArtifactAuthorityClass {
    Context,
    Presentation,
    CapabilityCandidate(CandidateKind),
}

enum CandidateKind {
    LeanProof,
    SyntheticExperiment,
}
```

The actual Rust types and constructors remain private. `CapabilityCandidate` means only that the
artifact may be submitted as one member of a later strict evidence-bundle validation. It is not a
capability and cannot support a claim by itself.

The operation must internally repeat persisted `artifact.verify` checks under the configured
project root. It must not accept a caller-provided `verified=true`, classification, producer
snapshot, capability ID, or prior kernel response as authority. Its request identifies only the
run and artifact through the versioned envelope. Its accepted result must include the run ID,
artifact ID, one authority class, an optional candidate kind, whether the result is compatibility
only, and a literal `authority_granted=false`. It must not return a verification label.

Classification precedence is fixed:

1. Reject any artifact that fails path, bytes, hash, same-run ledger, producing-commit, or exact
   commit-reference verification.
2. A `latex` artifact or a path ending in `.md`, `.markdown`, `.tex`, or `.pdf` is `Presentation`.
   Metadata cannot override this. An explicit `is_verification_evidence=true` on such an artifact
   remains a rejection performed by `artifact.verify`.
3. For other artifacts, explicit `is_verification_evidence=false` yields `Context`, even if an
   evidence-role string is present.
4. A `report` without an authority-bearing role is `Presentation`, preserving current manifest
   behavior. Candidate, score, log, and other non-presentation artifacts without an
   authority-bearing role are `Context`.
5. Literature and retrieval artifacts are always `Context` for claim-verification authority. The
   existing manifest may call them evidence for packaging, but they can never become proof or
   experiment capability candidates.
6. A linked `lean` artifact with role `proof` is a `CapabilityCandidate(LeanProof)`.
7. A linked `experiment` artifact with role `synthetic_experiment` is a
   `CapabilityCandidate(SyntheticExperiment)`.
8. Roles `fake_proof` and `fake_synthetic_experiment` produce the corresponding capability
   candidate only in `DevelopmentCompatibility`, with `compatibility_only=true` and
   `authority_granted=false`. In `StrictProduction` they are `Context` with a stable
   `fake_backend_denied` diagnostic; they are not rejected merely for existing in the research
   record.
9. Role `real_data_experiment` is `Context` in `DevelopmentCompatibility`. In `StrictProduction`,
   an attempt to present it as verification evidence is rejected with `data_regime_denied` because
   the current kernel cannot construct real-data authority.
10. An authority-bearing role on the wrong artifact type is rejected with `authority_denied`.
    Unknown roles and all LLM, reviewer, retrieval, citation, manuscript, prose, replay,
    diagnostics, and readiness roles are `Context`; an explicit request to treat one as authority
    is rejected with `authority_denied`.

An "explicit request to treat one as authority" means either
`metadata.is_verification_evidence=true` or use of an authority-bearing role. Absence of the
metadata field does not grant authority. The existing Python
`ArtifactRef.is_mvp_verification_evidence()` predicate is only an integrity/link eligibility check;
it is not the classifier and must not be translated as if it minted a capability candidate.

The only authority-bearing role/type pairs in this protocol version are:

| Artifact type | Evidence role | DevelopmentCompatibility | StrictProduction |
| --- | --- | --- | --- |
| `lean` | `proof` | Lean candidate | Lean candidate |
| `lean` | `fake_proof` | Compatibility-only Lean candidate | Context |
| `experiment` | `synthetic_experiment` | Synthetic candidate | Synthetic candidate |
| `experiment` | `fake_synthetic_experiment` | Compatibility-only synthetic candidate | Context |

Even the two strict-production candidates remain untrusted inputs to bundle validation. A `proof`
role does not establish a supported checker, and a `synthetic_experiment` role does not establish
the data regime, backend, success criteria, or metric validity.

Luna's differential matrix must cover every precedence rule above across both modes, including
presentation overrides, explicit false metadata, missing roles, fake roles, wrong type/role pairs,
literature context, unknown roles, unlinked artifacts, tampered bytes, corrupt ledgers, and
cross-run producers. Tests must separately assert that every accepted response has
`authority_granted=false` and contains no verification label or opaque capability.

A strict Lean capability requires all of:

- a valid proof contract for the exact candidate and claim;
- a non-fake supported proof backend;
- successful checker status and zero exit code;
- matching candidate and claim IDs;
- proof payload, stdout, and stderr hashes;
- no forbidden tokens or external/network dependency;
- linked raw proof/transcript and safety evidence;
- same-run artifact and ledger validation.

A strict synthetic capability requires all of:

- a valid synthetic experiment contract;
- `SyntheticOnly` data regime;
- fixed seed, declared metrics, acceptance criteria, and bounded replications;
- a non-fake allowed local backend and explicit external-tool gate;
- zero exit code and passed result;
- metrics satisfying the declared criteria;
- matching candidate, claim, and experiment IDs;
- linked input, output, raw trace, and safety artifacts with valid hashes;
- no real, public-download, or user-provided data dependency.

LLM, reviewer, retrieval, citation, manuscript, LaTeX, PDF, replay, diagnostics, and readiness
artifacts can never create these capabilities.

## Claim Authority

The kernel may decide whether a claim is admissible; it does not write or reinterpret the claim.

- `LeanVerified` requires a strict Lean capability for the exact claim.
- `SyntheticExperimentVerified` requires a strict synthetic capability and synthetic-scoped text.
- `RealDataExperimentVerified` and `ExperimentVerified` are always rejected in the current regime.
- `Conjecture`, `NegativeResult`, `Limitation`, and `Unsupported` do not require an evidence
  capability, but their existing section and main-text restrictions remain enforced.
- Citation support can justify bounded background attribution only, never experimental or proof
  verification.
- Human review records that a review occurred; it does not create scientific validation.
- Presentation and readiness artifacts cannot upgrade a claim.

The kernel returns a decision and reasons. It must never silently downgrade or upgrade a label;
Python may propose a separate explicit claim revision using the returned diagnostic.

## Threat Model

The kernel must fail closed against:

- malformed or extra protocol fields;
- protocol-version drift;
- forged evidence-role metadata;
- tampered files, sidecars, manifests, or SQLite rows;
- missing, stale, cross-run, or circular references;
- duplicate roots, forks, broken parents, and concurrent non-tip appends;
- path traversal, absolute paths, symlink escape, and unsafe extensions;
- presentation artifacts relabeled as evidence;
- fake backends presented as production evidence;
- partial external-process output and mismatched result contracts;
- crashes between artifact, ledger, and sidecar writes;
- diagnostics that expose secrets or raw credentials.

Out of scope are a compromised operating system, an attacker with unrestricted write access while
verification is running, correctness of Lean's kernel, correctness of scientific methodology, and
truth of real-world interpretations.

## Stable Diagnostics

Kernel diagnostics need stable codes independent of prose. The initial taxonomy is:

- `protocol_invalid`
- `protocol_version_mismatch`
- `path_invalid`
- `path_escape`
- `hash_invalid`
- `hash_mismatch`
- `artifact_missing`
- `artifact_unlinked`
- `artifact_commit_mismatch`
- `ledger_parent_missing`
- `ledger_cross_run_parent`
- `ledger_non_tip_append`
- `ledger_fork`
- `ledger_hash_mismatch`
- `authority_denied`
- `claim_scope_denied`
- `fake_backend_denied`
- `data_regime_denied`
- `checkpoint_incomplete`
- `io_failure`
- `sqlite_failure`
- `internal_error`

Diagnostics may include bounded IDs, expected/observed categories, and safe paths relative to the
project root. They must not include credentials, authorization headers, environment secrets, or
unbounded raw adapter payloads.

## Compatibility and Cutover Gates

Rust remains non-authoritative until all gates pass:

1. Selected protocol schemas and examples validate in Python and Rust.
2. Canonical JSON and commit hashes match the golden corpus byte-for-byte.
3. Existing SQLite ledgers load and verify identically.
4. Artifact and evidence decisions match in `DevelopmentCompatibility` shadow mode.
5. Strict-production tests prove fake and presentation artifacts cannot mint capabilities.
6. Differential tests cover all accepted and rejected fixtures in the selected module set.
7. Fault-injection tests cover temp writes, fsync/replace failures, SQLite rollback, and sidecar
   interruption.
8. Python `pytest` and Ruff pass; Rust formatting, Clippy, unit, property, and integration tests pass.
9. Shadow mode reports zero unexplained decision mismatches over representative completed runs.
10. Sol reviews any semantic difference before one Rust operation becomes authoritative.

Cutover occurs operation by operation. Python fallbacks must be explicit and diagnostic; silent
fallback is forbidden. Python implementations are not deleted until the corresponding Rust path
has survived at least one protocol-version transition.

## Model Ownership

The work allocation target is approximately 82 percent Luna and 18 percent Sol.

Sol owns only:

- this trust-boundary and authority contract;
- decisions about capability constructors and legal evidence transitions;
- canonical-byte and ledger semantic review;
- threat-model review;
- review of semantic mismatches and final cutover.

Luna owns:

- exact module and schema inventory;
- Rust workspace, crate, dependency, and CI scaffolding;
- selected protocol bindings and generated-code evaluation;
- canonical serializer implementation and golden fixtures;
- artifact, ledger, evidence, claim, checkpoint, and replay translation;
- unit, property, differential, fault-injection, and integration tests;
- Python subprocess bridge and shadow mode;
- diagnostics, documentation, bug fixes, and performance work;
- protocol export/version updates required by new envelopes.

Luna must stop and request Sol review if:

- Python and Rust require different hash bytes;
- a protocol ambiguity affects evidence authority;
- a capability would need a public unchecked constructor;
- a proposed fix weakens fail-closed behavior;
- ledger ordering, transaction, or parent semantics would change;
- a schema change could make a previously forbidden label constructible;
- a mismatch is resolved by changing expected tests rather than explaining semantics;
- unsafe Rust, network access, arbitrary subprocess execution, or a new persistence engine is
  proposed.

## Luna Handoff

Sol's initial task ends with this document. The next task belongs to Luna:

1. inventory the exact Python functions and transitive protocol schemas for read-only operations;
2. propose the minimal Rust workspace and dependency set;
3. add kernel envelope models in Python and export them through the normal protocol process;
4. construct canonical JSON and ledger-hash golden fixtures;
5. implement read-only protocol and hash parity before any ledger mutation.

No Rust crate, generated binding, FFI layer, or translated implementation should be attributed to
this Sol design phase.
