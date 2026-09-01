# Rust Research Kernel Contract

## Status

This document is the Sol-owned design baseline for the staged Rust translation of fActorI's
deterministic trust kernel. It freezes the intended trust boundary, authority rules, compatibility
requirements, and model handoff. The current Rust implementation is a read-mostly compatibility
kernel with one approved JSON-only artifact mutation. It has not received evidence authority,
ledger authority, or general pipeline-mutation authority.

Current protocol baseline: `0.87.0`. The next frozen ledger slice targets `0.88.0`.

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
- `evidence.validate_bundle`: validate one persisted proof or synthetic-experiment bundle and
  construct a private, request-local capability without returning claim authority;
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

### `evidence.validate_bundle` Semantic Freeze

`evidence.validate_bundle` is the next read-only kernel operation. It validates one complete
persisted Stage C bundle. It may construct an `EvidenceCapability` internally, but the capability
is private, request-local, and dropped before returning. The response must not contain a
capability ID, bearer token, verification label, serialized capability, or reusable attestation.
It always contains literal `authority_granted=false`.

An accepted response means only that the named persisted bundle satisfied the frozen structural,
integrity, backend, and result rules. It does not authorize a manuscript claim. Future
`claim.resolve` must receive the persisted bundle locator and repeat bundle validation in the same
request; it must not accept a prior bundle-validation response as authority.

The operation is new and requires a protocol minor-version bump. Luna owns the exact Pydantic
model names, but the wire payload must preserve this logical shape:

```text
run_id
candidate_id
claim_id
producing_commit_hash
bundle:
  kind = LeanProof | SyntheticExperiment
  role-specific artifact IDs
```

The caller supplies artifact IDs and one producing-commit hash, not `ArtifactRef` objects,
metadata, paths, hashes, a prior classification, or a `verified=true` assertion. Rust loads the
commit from the run's read-only SQLite ledger and resolves the complete references itself.

The Lean variant names exactly five distinct members:

```text
contract_artifact_id
payload_artifact_id
trace_artifact_id
result_artifact_id
safety_artifact_id
```

The synthetic variant names exactly six distinct members:

```text
contract_artifact_id
input_artifact_id
trace_artifact_id
output_artifact_id
result_artifact_id
safety_artifact_id
```

Before parsing scientific payloads, the validator must:

1. validate the request envelope and safe identifier/hash grammar;
2. open the configured run ledger read-only and verify the complete linear chain;
3. resolve exactly one same-run producing commit by hash;
4. require the commit candidate ID to equal the request candidate ID;
5. require action `StageCProofValidated` for Lean or `StageCSyntheticExperimentRun` for synthetic;
6. require the commit's artifact-ID set to equal the named bundle member set, with no missing,
   duplicate, cross-commit, or unlisted members;
7. repeat full path, byte-hash, metadata, and exact producer-link verification for every member;
8. require JSON artifacts, reject duplicate JSON keys, and parse each member against its selected
   closed protocol schema;
9. reject presentation artifacts and any fake, compatibility-only, retrieval, LLM, reviewer,
   manuscript, citation, replay, diagnostics, or readiness member.

All bundle members therefore originate in one immutable Stage C commit. A valid-looking result
cannot be combined with a contract, trace, output, or safety artifact from another attempt.

The contract and result members already have public strict models. Luna must add closed companion
models for the proof payload, proof trace, proof safety report, experiment input, experiment trace,
experiment output, and experiment safety report before implementing Rust parsing. These models may
preserve bounded domain-specific maps where the current wire format requires them, but the
surrounding object must reject unknown top-level fields. Rust must not deserialize these members
directly into generic JSON maps.

For every member, metadata equality means equality of the complete persisted metadata map, not
presence of a trusted subset. The only accepted maps are:

| Bundle members | Exact metadata |
| --- | --- |
| Lean contract, payload, safety | `format=json`, `stage=stage_c`, `backend=lean`, `provider=lean`, `is_verification_evidence=false`, `fake=false` |
| Lean trace, result | the preceding map with `evidence_role=proof` and `is_verification_evidence=true` |
| Synthetic contract, input, safety | `format=json`, `stage=stage_c`, `backend=local_synthetic`, `provider=local`, `is_verification_evidence=false`, `fake=false` |
| Synthetic trace, output, result | the preceding map with `evidence_role=synthetic_experiment` and `is_verification_evidence=true` |

No extra metadata key is accepted. In particular, caller-supplied authority flags, alternate roles,
compatibility markers, or presentation metadata cannot be ignored as harmless decoration.

The accepted result contains only:

```text
run_id
candidate_id
claim_id
bundle_kind
producing_commit_hash
validated_artifact_ids
bundle_valid = true
authority_granted = false
```

`validated_artifact_ids` is emitted in the fixed role order above. Rejected and error responses
have an empty result and stable diagnostics. Both kernel modes apply the same strict bundle rules:
fake bundles are rejected with `fake_backend_denied` even in `DevelopmentCompatibility` because
compatibility classification never constructs a strict capability.

#### Strict Lean bundle

A strict Lean capability requires all of:

- a valid proof contract for the exact candidate and claim;
- contract language `Lean`, backend `lean`, non-empty tool name, `allow_external_tools=true`,
  `allow_external_calls=false`, `fake_default=false`, and `is_verification_evidence=false`;
- a non-empty proof payload with no forbidden token, network marker, absolute external import, or
  undeclared external dependency;
- payload-artifact candidate, claim, language, and proof text exactly matching the contract;
- result candidate and claim IDs matching the request and contract;
- result backend/provider `lean`, matching language/tool fields, `fake=false`, `verified=true`,
  `exit_code=0`, `forbidden_tokens_present=false`, and label `LeanVerified`;
- result trace and safety artifact IDs matching the named bundle members;
- trace backend/provider/tool/exit fields matching the result, with `fake=false`;
- SHA-256 of the exact proof text, trace stdout, and trace stderr matching the result hashes;
- safety candidate and claim IDs matching, `contract_valid=true`, `result_valid=true`, empty
  reason lists, `fake=false`, and `is_verification_evidence=false`;
- exact Stage C metadata for the backend/provider and JSON format; trace and result have role
  `proof`, `is_verification_evidence=true`, and `fake=false`, while contract, payload, and safety
  remain non-evidence context;
- commit payload backend/provider, contract ID, result ID, candidate, claim, result fields, and
  trace/safety links matching the resolved bundle.

The proof checker remains the authority for whether Lean accepted the payload. Rust verifies that
the persisted record faithfully and safely represents that checker result; Rust does not re-prove
the theorem or infer that the formal statement matches an informal scientific claim.

Import checking is deliberately conservative. After normalizing line endings, every non-comment
line whose first token is `import` must contain only bounded Lean module identifiers and every
listed module must occur exactly in `allowed_imports`. A malformed import command, path separator,
URL marker, or imported module absent from the allowlist rejects the bundle. This scanner is a
safety gate, not a Lean parser; uncertainty rejects rather than broadening the accepted language.

#### Strict synthetic bundle

A strict synthetic capability requires all of:

- a valid synthetic experiment contract;
- contract backend `local_synthetic`, non-empty runner name, `SyntheticOnly` data regime, a
  supported synthetic experiment kind, `allow_external_tools=true`, `allow_external_calls=false`,
  `fake_default=false`, and `is_verification_evidence=false`;
- non-empty synthetic-data, metric, and acceptance-criteria specifications; a fixed seed;
  replications in `[1, 100]`; timeout in `[1, 60]`; and explicit prohibition of public-download,
  user-provided, real-world, network, and absolute-path inputs;
- input artifact exactly equal to the deterministic runner-input projection of the contract;
- result candidate, claim, and experiment IDs matching the request and contract;
- result backend/provider `local_synthetic`/`local`, matching experiment kind, data regime, and
  runner fields, `fake=false`, `passed=true`, `exit_code=0`, and label
  `SyntheticExperimentVerified`;
- result trace and safety artifact IDs matching the named bundle members;
- trace backend/provider/runner/exit fields matching the result, with `fake=false`;
- SHA-256 of canonical input JSON, canonical output JSON, exact trace stdout, and exact trace
  stderr matching the result hashes;
- output metrics exactly matching the finite result metrics and every declared acceptance metric
  satisfying a non-empty numeric `min`, `max`, or scalar-minimum rule;
- result acceptance criteria exactly matching the contract criteria;
- safety candidate and claim IDs matching, `contract_valid=true`, `result_valid=true`, empty
  reason lists, `fake=false`, and `is_verification_evidence=false`;
- exact Stage C metadata for backend/provider and JSON format; trace, output, and result have role
  `synthetic_experiment`, `is_verification_evidence=true`, and `fake=false`, while contract, input,
  and safety remain non-evidence context;
- commit payload backend/provider, contract ID, result ID, candidate, claim, experiment, result
  fields, and trace/safety links matching the resolved bundle.

Unknown acceptance-rule keys, empty bound maps, non-finite metrics, Boolean-as-number coercion,
real/public/user data markers, absolute paths, and network markers are rejected. A successful
synthetic bundle supports only its declared synthetic experiment and never real-world empirical
validation.

LLM, reviewer, retrieval, citation, manuscript, LaTeX, PDF, replay, diagnostics, and readiness
artifacts can never create these capabilities.

#### Failure and test matrix

Stable bundle-specific diagnostics are:

- `bundle_member_missing`
- `bundle_member_duplicate`
- `bundle_member_unexpected`
- `bundle_commit_mismatch`
- `bundle_candidate_mismatch`
- `bundle_claim_mismatch`
- `bundle_contract_invalid`
- `bundle_backend_denied`
- `bundle_result_invalid`
- `bundle_trace_hash_mismatch`
- `bundle_payload_hash_mismatch`
- `bundle_output_hash_mismatch`
- `bundle_safety_invalid`
- `bundle_metrics_invalid`

Existing artifact, ledger, fake-backend, data-regime, protocol, and authority diagnostics remain
applicable and should be preferred when they identify the lower-level cause.

Luna's tests must include one valid real Lean bundle and one valid local synthetic bundle produced
through the existing injected-runner Stage C paths. For every required field or relationship, a
single-mutation negative fixture must fail closed in both modes. Additional fixtures must cover
member substitution across candidates, claims, commits, and runs; fake bundles; presentation
members; malformed and duplicate-key JSON; tampered bytes and SQLite rows; unsafe contracts;
mismatched raw hashes; invalid safety reports; unmet or malformed metric criteria; non-finite and
Boolean metric coercions; missing and extra members; and unknown fields.

Compatibility comparison is asymmetric: Rust must never accept a bundle rejected by the existing
Python safety validators. Rust may reject additional ambiguous bundles in strict validation, but
each difference requires a named test and Sol review. No expected Python test may be weakened to
make Rust pass.

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

### `claim.resolve` Semantic Freeze

`claim.resolve` is a read-only decision over one persisted claim-table record. The caller may
identify the run, claim-table artifact, claim ID, and optional strict evidence bundle, but it must
not supply claim text, label, section, main-text permission, evidence status, a prior kernel
response, or any reusable capability. Rust resolves those fields from the hash-linked claim table.

The wire payload is:

```text
run_id
claim_id
claim_table:
  artifact_id
  producing_commit_hash
evidence: null | {
  producing_commit_hash
  bundle:
    kind = LeanProof | SyntheticExperiment
    role-specific artifact IDs
}
```

Before deciding admissibility, Rust must:

1. validate safe identifier and hash grammar and require a configured project root;
2. load and verify the complete persisted run ledger;
3. resolve the named `ClaimTableBuilt` commit and require exactly one `report` artifact with the
   requested ID, matching producing-commit link, confined path, content hash, and exact metadata
   `format=json`, `stage=manuscript_planning`, `fake=true`;
4. parse the artifact as a closed `ClaimTable`, reject duplicate claim IDs, and require its parsed
   value to equal the producing commit payload;
5. resolve exactly one claim with the requested claim ID and validate its candidate ID, label,
   section, non-empty text, and evidence-artifact IDs;
6. when evidence is supplied, repeat strict `evidence.validate_bundle` validation in the same
   request for the persisted claim's candidate and claim IDs;
7. require the claim's evidence-artifact-ID set to equal the authority-bearing members of that
   strict bundle: Lean trace and result, or synthetic trace, output, and result.

The claim table is identity/context, not evidence. Its `fake=true` metadata records deterministic
construction and cannot mint a capability. Only the same-request strict bundle validation can
support a verified label.

Admissibility is fixed as follows:

- `LeanVerified` requires a matching strict Lean bundle;
- `SyntheticExperimentVerified` requires a matching strict synthetic bundle, section `Abstract`,
  `Synthetic Experiments`, or `Results`, and a persisted claim text containing the complete token
  `synthetic` or `simulation` while containing none of `real-world`, `real world`, `empirical
  validation`, `external validity`, `deploy`, `deployment`, `generalize`, `generalization`, or
  `universal`;
- `ExperimentVerified` and `RealDataExperimentVerified` are inadmissible;
- `Conjecture` is restricted to `Theory`, `Future Work`, or `Appendix`;
- `NegativeResult` is restricted to `Negative Results`, `Results`, or `Limitations`;
- `Limitation` is restricted to `Limitations`;
- `Unsupported` is restricted to `Future Work` with `allowed_in_main_text=false`;
- any claim with `allowed_in_main_text=false` is inadmissible in an existing main-text section.

The accepted result contains only:

```text
run_id
candidate_id
claim_id
claim_text_hash
claim_label
allowed_in_main_text
allowed_section
claim_record_validated = true
admissible
evidence_bundle_validated
authority_granted = false
```

`claim_text_hash` is SHA-256 over the exact UTF-8 persisted claim text. Rejected and error
responses have an empty result. An accepted response with `admissible=false` is a completed bounded
decision, not transport failure and not a label downgrade.

Stable claim-resolution diagnostics are:

- `claim_record_missing`
- `claim_record_invalid`
- `claim_evidence_missing`
- `claim_evidence_mismatch`
- `claim_scope_denied`
- `claim_not_admissible`

Existing protocol, ledger, artifact, bundle, fake-backend, data-regime, and authority diagnostics
remain applicable and should be preferred for their lower-level causes.

### `checkpoint.verify` Scope Decision

The first `checkpoint.verify` implementation is deliberately limited to the immutable autonomous-
paper checkpoint chain written by `AutonomousPaperCheckpointWritten` commits. This is the only
current checkpoint representation with a persisted self-hash, predecessor link, output-hash map,
ledger-tip locator, immutable index snapshots, and explicit resume policy.

The following objects remain outside this operation:

- `StageCheckpoint`, which is a read-only file-existence diagnostic and is not provenance;
- `TargetedStudyCheckpoint`, which is a resumable orchestration snapshot but does not yet contain
  the self-hash and predecessor-chain material required by this kernel contract;
- replay reports, manifests, release reports, and final-paper bundles, which receive separate
  integrity decisions and cannot stand in for a checkpoint chain.

Supporting either excluded checkpoint format requires a later protocol extension with its own
discriminator and semantic freeze. Luna must not generalize this operation by accepting arbitrary
checkpoint JSON or caller-supplied output inventories.

### `checkpoint.verify` Semantic Freeze

`checkpoint.verify` is a read-only integrity and resume-policy decision over the latest persisted
autonomous-paper checkpoint index and the complete checkpoint chain it names. The caller may locate
the run and latest index artifact, but it must not supply checkpoint paths, checkpoint contents,
output hashes, stage status, safety status, resume permission, a prior kernel response, or a reusable
capability.

The initial wire payload is:

```text
run_id
index:
  artifact_id
  producing_commit_hash
```

The locator commit must be the latest `AutonomousPaperCheckpointWritten` commit in the verified
run ledger. Selecting an older, merely valid index is rejected rather than interpreted as a partial
resume request.

Before returning a decision, Rust must:

1. validate safe run/artifact identifiers and a lowercase SHA-256 commit hash, and require a
   configured project root;
2. load and verify the complete persisted run ledger using the frozen ledger contract;
3. resolve the requested producing commit, require action `AutonomousPaperCheckpointWritten`, and
   prove that no later commit with that action exists in the run;
4. require that commit to contain exactly one numbered checkpoint `report` artifact and its matching
   numbered checkpoint-index `report` artifact, with confined canonical paths, exact producing-
   commit links, raw-byte hashes, and exact metadata:
   `format=json`, `stage=autonomous_paper_checkpoint`,
   `artifact_role=controller_reliability_context`, and all four scientific/publication authority
   flags `false`;
5. parse the located index as a closed `AutonomousPaperCheckpointIndex`; require the run ID,
   authority flags, positive count, unique path inventory, and `checkpoint_count` to agree; and
   require paths to be the consecutive canonical checkpoint paths `0001..N` in index order;
6. resolve every named checkpoint through its own earlier-or-equal
   `AutonomousPaperCheckpointWritten` commit, requiring the matching historical index snapshot,
   exact artifact identity/metadata/hash/link checks, and the same two-artifact commit shape;
7. parse every checkpoint as a closed `AutonomousPaperCheckpoint`, require its companion artifact
   ID/path number, run ID, controller ID, closed stage name, non-empty timestamps, current protocol
   version, authority flags, stage/output inventory, and producing-commit payload to agree;
8. recompute each `checkpoint_hash` as Python-compatible canonical JSON SHA-256 over the complete
   checkpoint object with only `checkpoint_hash` omitted;
9. require the first checkpoint to have an empty `input_hashes` map and each later checkpoint to
   have exactly `previous_checkpoint=<preceding checkpoint hash>` using lowercase SHA-256 grammar;
10. require each checkpoint's `ledger_tip_hash_optional` to equal its producing commit's parent hash,
    rather than merely naming any commit somewhere in the ledger;
11. require `stage_artifact_paths` and `output_hashes` to have identical unique path sets, confine
    every output beneath `runs/<run_id>/`, reject symlinks in any path component, read raw bytes,
    and match every output SHA-256; an output need not itself be evidence or a ledger artifact;
12. validate every historical index snapshot as the exact prefix ending at its companion checkpoint,
    and require the latest index controller ID, stage, count, inventory, declared resume state, and
    blockers to agree with the fully derived chain state.

Checkpoint and index artifacts are controller reliability context only. Their hashes can establish
that a specific prior output is unchanged; they cannot establish that the output is scientifically
correct, evidence-bearing, publication-ready, or safe for any use beyond the exact bounded resume
decision.

Integrity and resume policy are separate:

- missing, malformed, stale, unlinked, non-latest, path-escaping, symlinked, hash-mismatched, or
  authority-claiming checkpoint state is rejected with an empty result;
- a structurally valid chain containing a coherently persisted failed/blocked checkpoint is accepted
  with `resume_allowed=false` and a stable non-reusable diagnostic;
- a reusable checkpoint requires safety status `passed` or `passed_with_warnings`, stage status
  `completed`, `completed_with_warnings`, or `reused`, `verified_for_resume=true`, verification
  status `verified` or `verified_with_warnings`, and no verification errors;
- a coherently non-reusable checkpoint requires safety status `failed`, stage status `blocked` or
  `failed`, `verified_for_resume=false`, and verification status `failed`; it never grants
  permission to reuse that stage or any later stage;
- index `resume_allowed` and `resume_blockers` are assertions to validate against the derived state,
  not trusted inputs.

The accepted result contains only:

```text
run_id
checkpoint_index_artifact_id
checkpoint_index_producing_commit_hash
checkpoint_count
validated_checkpoint_hashes
latest_checkpoint_hash
latest_completed_stage
validated_output_count
checkpoint_chain_valid = true
resume_allowed
authority_granted = false
```

`validated_checkpoint_hashes` preserves index order. `validated_output_count` counts validated
checkpoint output entries, including the same immutable output if deliberately referenced by more
than one checkpoint. `authority_granted=false` means no evidence, label, scientific-validation,
human-approval, or publication authority is returned; `resume_allowed=true` is only an operational
decision for this exact hash-locked chain.

Stable checkpoint diagnostics are:

- `checkpoint_index_missing`
- `checkpoint_index_invalid`
- `checkpoint_not_latest`
- `checkpoint_record_missing`
- `checkpoint_record_invalid`
- `checkpoint_chain_mismatch`
- `checkpoint_hash_mismatch`
- `checkpoint_protocol_mismatch`
- `checkpoint_ledger_mismatch`
- `checkpoint_output_missing`
- `checkpoint_output_path_invalid`
- `checkpoint_output_hash_mismatch`
- `checkpoint_authority_violation`
- `checkpoint_not_reusable`

Existing protocol, ledger, artifact-path, artifact-hash, and transport diagnostics remain applicable
and should be preferred when they identify a lower-level cause precisely.

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
- `bundle_member_missing`
- `bundle_member_duplicate`
- `bundle_member_unexpected`
- `bundle_commit_mismatch`
- `bundle_candidate_mismatch`
- `bundle_claim_mismatch`
- `bundle_contract_invalid`
- `bundle_backend_denied`
- `bundle_result_invalid`
- `bundle_trace_hash_mismatch`
- `bundle_payload_hash_mismatch`
- `bundle_output_hash_mismatch`
- `bundle_safety_invalid`
- `bundle_metrics_invalid`
- `checkpoint_incomplete`
- `checkpoint_index_missing`
- `checkpoint_index_invalid`
- `checkpoint_not_latest`
- `checkpoint_record_missing`
- `checkpoint_record_invalid`
- `checkpoint_chain_mismatch`
- `checkpoint_hash_mismatch`
- `checkpoint_protocol_mismatch`
- `checkpoint_ledger_mismatch`
- `checkpoint_output_missing`
- `checkpoint_output_path_invalid`
- `checkpoint_output_hash_mismatch`
- `checkpoint_authority_violation`
- `checkpoint_not_reusable`
- `replay_not_complete`
- `replay_not_latest`
- `replay_snapshot_changed`
- `replay_required_output_missing`
- `replay_required_output_invalid`
- `replay_manifest_missing`
- `replay_manifest_invalid`
- `replay_manifest_mismatch`
- `replay_dependency_missing`
- `replay_dependency_ambiguous`
- `replay_dependency_mismatch`
- `replay_authority_violation`
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

## Initial Luna Handoff (Completed)

Sol's initial task ends with this document. The next task belongs to Luna:

1. inventory the exact Python functions and transitive protocol schemas for read-only operations;
2. propose the minimal Rust workspace and dependency set;
3. add kernel envelope models in Python and export them through the normal protocol process;
4. construct canonical JSON and ledger-hash golden fixtures;
5. implement read-only protocol and hash parity before any ledger mutation.

No Rust crate, generated binding, FFI layer, or translated implementation should be attributed to
this Sol design phase.

## Completed Luna Handoff: Strict Evidence Bundles

Sol's strict-bundle design task ends with the semantic freeze above. The next implementation task
belongs to Luna:

1. inventory the exact persisted proof and synthetic bundle bytes, metadata, commit payloads, and
   transitive schemas from representative injected-runner Stage C fixtures;
2. propose the minimal closed Pydantic request/result models for `evidence.validate_bundle`, bump
   the protocol minor version, add the seven closed companion artifact models named above, export
   schemas, and add valid/invalid protocol examples;
3. implement Rust locator resolution and private request-local `EvidenceCapability` constructors
   without exposing a capability token or changing ledger/persistence semantics;
4. port the frozen Lean and synthetic checks as small deterministic functions;
5. add Python/Rust differential, single-mutation, corruption, cross-run, and both-mode tests;
6. add a read-only Python shadow bridge that checks response identity and literal
   `authority_granted=false`;
7. run focused protocol, Rust, parity, Ruff, and then full-suite verification before Sol review.

Luna must stop before implementation if the current Python artifacts cannot satisfy the exact
single-commit member sets, if a required relationship is absent from persisted bytes, if accepting
a bundle would require trusting caller metadata or a prior kernel response, or if any valid
fixture would require weakening a frozen authority rule. `claim.resolve`, mutation authority,
PyO3, networking, process execution, and changes to Stage C persistence were outside that handoff.

## Completed Luna Handoff: Claim Resolution

Sol's claim-resolution design task ends with the semantic freeze above. Luna owns the bounded
implementation:

1. replace caller-authored claim fields with the persisted claim-table locator;
2. add closed claim-table wire models and read-only claim-record verification;
3. reuse strict same-request evidence-bundle validation and bind exact evidence-member IDs;
4. return only the frozen non-authoritative result shape;
5. add valid Lean and synthetic fixtures, single-mutation claim-table tests, synthetic-scope tests,
   raw transport-schema tests, bridge identity tests, and Python/Rust differential tests;
6. regenerate protocol schemas/examples and run focused Rust, protocol, parity, Ruff, and full-suite
   verification before final Sol review.

Luna must stop if claim identity cannot be resolved from immutable persisted bytes, if accepting a
claim would require trusting caller-authored text or labels, or if a valid fixture requires
weakening strict evidence-bundle validation.

## Luna Implementation Submitted: Autonomous Checkpoint Verification

Sol's checkpoint-verification design task ends with the semantic freeze above. The bounded
implementation includes:

1. inventory representative successful, warning, failed-handoff, crash-resume, and resumed
   autonomous-paper checkpoint chains, including exact artifact metadata and commit payloads;
2. add the locator-only `checkpoint.verify` request/result protocols and closed transport copies of
   the autonomous checkpoint/index records, then bump the protocol minor version to `0.85.0` and
   regenerate schemas/examples through `factori export-protocols`;
3. implement latest-index resolution, full-ledger verification, historical prefix/index validation,
   checkpoint self-hash and predecessor checks, parent-tip binding, output confinement/hash checks,
   and derived resume policy in Rust;
4. add a read-only Python shadow bridge that verifies request identity, exact ordered checkpoint
   hashes, `checkpoint_chain_valid=true`, and literal `authority_granted=false`;
5. add Python/Rust differential tests for successful and coherently blocked chains plus
   single-mutation tests for stale index selection, count/path/order changes, checkpoint and output
   hash corruption, predecessor breaks, wrong parent tip, stale protocol, authority-flag inversion,
   unsafe paths, symlinks, missing outputs, malformed closed records, and unexpected fields;
6. run protocol export/check, example validation, Rust format/Clippy/tests, focused parity tests,
   Ruff, and the complete Python suite before final Sol review.

Luna must stop before implementation if current successful and failed checkpoint fixtures cannot
satisfy the frozen two-artifact commit shape, if any checkpoint output necessarily escapes the run,
if the current writer does not preserve exact historical index prefixes, or if matching Python
behavior would require trusting filesystem discovery instead of the hash-linked ledger locator.
Luna must not modify checkpoint persistence, broaden the operation to targeted/generic checkpoints,
add mutation authority, or treat resume permission as scientific authority in this handoff.

## Sol Review of Protocol 0.85.0: Changes Requested

Sol reviewed the committed `checkpoint.verify` implementation after protocol export, Rust/Python
parity work, and the complete test run. The following parts match the frozen design and are
approved:

- the locator-only request and non-authoritative result shapes;
- the `0.85.0` minor version bump and five new standalone schemas;
- the three compatibility-checker warnings caused by adding one discriminator mapping, one request
  `oneOf` arm, and one response-result `anyOf` arm; these are reviewed additive changes, not
  permission to weaken future unknown-change review;
- latest-commit resolution, complete ledger verification, two-artifact checkpoint commit shape,
  closed checkpoint/index parsing, canonical self-hashes, predecessor links, parent-tip binding,
  output confinement and hashes, authority-flag rejection, and exact historical index prefixes;
- the read-only Python shadow bridge and literal `authority_granted=false` boundary.

Operation-level cutover is not approved. Rust currently assigns `latest_resume_allowed` from each
checkpoint in turn. A coherent non-reusable checkpoint can therefore be followed by a reusable
checkpoint whose state overwrites the earlier failure and produces `resume_allowed=true`. That
contradicts the frozen rule that a failed checkpoint cannot authorize reuse of its stage or any
later stage.

The production writer never creates a later checkpoint after a terminal failed checkpoint: a
failed controller path writes the failed/blocked handoff checkpoint last, and resume verification
rejects that chain. The accepted chain grammar is therefore clarified without changing the public
schema:

- zero or more reusable checkpoints may precede one coherent terminal failed/blocked checkpoint;
- a coherent terminal failed/blocked checkpoint must be the final checkpoint in the index;
- that terminal chain is accepted with `resume_allowed=false`, the declared blocker for the final
  stage, and `checkpoint_not_reusable`;
- any checkpoint after a coherent terminal failure is an impossible writer state and must be
  rejected with `checkpoint_chain_mismatch`, even if all hashes, links, and later status fields are
  internally consistent;
- a fully reusable chain remains accepted with `resume_allowed=true`.

The existing failed-chain parity fixture is not representative because it writes two consecutive
failed checkpoints. Representative warning and resumed-controller chains are also not covered by
the Rust shadow tests required by the handoff. Consequently cutover gates 6, 9, and 10 remain open.
Rust remains a read-only shadow implementation, and Sol does not freeze `replay.verify_core` while
this correction is outstanding.

## Current Luna Correction Handoff: Checkpoint Chain State

Luna owns only the bounded correction below:

1. derive checkpoint-chain resume state monotonically and reject every checkpoint appearing after
   the first coherent terminal failed/blocked checkpoint with `checkpoint_chain_mismatch`;
2. replace the two-failure acceptance fixture with a writer-produced reusable prefix followed by
   one terminal failed/blocked checkpoint, and require accepted `resume_allowed=false` plus exactly
   one `checkpoint_not_reusable` diagnostic;
3. add a re-sealed, fully hash-consistent failed-then-reusable chain and prove it is rejected rather
   than allowing the later checkpoint to restore resume permission;
4. add accepted `passed_with_warnings` / `completed_with_warnings` coverage;
5. add a representative crash-resume or resumed-controller chain with a changed controller ID and
   repeated downstream stage names, and verify the exact ordered hashes through the Python shadow
   bridge;
6. exercise the same accepted/rejected resume decisions in both authority modes, because mode must
   not alter checkpoint integrity or operational resume policy;
7. rerun protocol currentness and examples, Rust format/Clippy/tests, focused parity, Ruff, and the
   complete Python suite before returning to Sol.

This correction does not require a protocol bump if request, response, schema, and diagnostic
shapes remain unchanged. Luna must not change checkpoint persistence, reinterpret a terminal
failure as recoverable, begin `replay.verify_core`, or claim operation-level cutover. Any need to
support a post-failure checkpoint sequence must return to Sol as a new persisted-state design.

## Final Sol Review of `checkpoint.verify`: Approved

Sol reviewed Luna commits `859373b` and `cc73bcd` against the semantic freeze and the correction
handoff. The correction makes terminal checkpoint state monotonic: a reusable prefix may end in one
coherent failed/blocked checkpoint, but every later checkpoint is rejected with
`checkpoint_chain_mismatch` before it can restore resume permission. The representative terminal
failure is accepted with `resume_allowed=false`, literal `authority_granted=false`, and exactly one
`checkpoint_not_reusable` diagnostic.

The parity inventory now covers successful, warning, terminal-failure, re-sealed
failed-then-reusable, and resumed-controller chains, including repeated downstream stages, changed
controller IDs, exact ordered checkpoint hashes through the Python bridge, malformed inventory
paths, and matching accepted/rejected decisions in both kernel modes. Protocol `0.85.0` remains
current because no request, response, schema, or diagnostic shape changed. The completed validation
matrix includes protocol export/version/example checks, Rust format/Clippy/tests, Ruff, 150 kernel
parity tests, and the complete 1,505-test Python run performed before the final test-only mode
parameterization.

Cutover gates 6, 9, and 10 are closed for this operation. `checkpoint.verify` is approved for the
bounded read-only integrity and operational-resume decision defined above. This approval does not
make checkpoint artifacts evidence, grant claim or publication authority, change checkpoint
persistence, authorize post-failure continuation, or approve any mutating Rust operation. Python
fallbacks remain explicit until the corresponding call site deliberately cuts over; silent
fallback remains forbidden.

## `replay.verify_core` Scope Decision

The first replay operation is a fail-closed verification of the immutable mechanical core of one
completed run. It is not a Rust port of the complete Python `ReplayVerificationReport`. The Python
replay layer continues to own narrative completeness, failed/deferred branch presentation,
blocked-claim appendix checks, final-audit/release/export policy comparison, human-readable
findings, summaries, and optional report writing.

Rust owns only these core questions in this phase:

- is the caller naming the current complete ledger snapshot for the requested run;
- is that complete ledger linear, hash-valid, single-rooted, single-tipped, and run-local;
- do all ledger artifact references resolve to exact confined immutable bytes and exact producing
  commits;
- is the persisted artifact manifest an exact derived inventory of the ledger prefix that existed
  immediately before the manifest commit;
- do the required completed-run outputs and the claim/evidence dependency records exist without
  ambiguity;
- do persisted labels, dependencies, presentation flags, and derived-report paths preserve the
  current MVP authority boundary.

The operation must not execute pipeline commands, regenerate outputs, discover unledgered files,
repair the ledger, write replay reports, create ledger commits, update manifests, call adapters,
re-run proof or experiment tools, or infer scientific correctness from hash equality.

### Frozen Request and Snapshot

The caller supplies only:

```text
run_id
ledger_tip_hash
```

Both fields are locators, not authority-bearing assertions. `ledger_tip_hash` must be a lowercase
SHA-256 digest and must equal the sole current tip of the persisted run ledger. Rust loads the
ledger from `runs/<run_id>/ledger.sqlite` through the configured project root in read-only mode; the
caller must not supply commits, artifact references, paths, manifest entries, claim fields,
expected counts, replay status, or prior kernel output.

An older valid tip is rejected with `replay_not_latest`. Rust records the loaded tip and commit
count and checks them again before returning. A concurrent append or other snapshot change is
rejected with `replay_snapshot_changed`; it is never interpreted as a successful verification of a
partial run.

The run is complete for this bounded operation only when the verified ledger contains an
`ExportReadinessReportWritten` commit whose payload contains `ready_for_polished_prose`, whose JSON
artifact reference and raw bytes are valid, and which is at or before the requested current tip.
The later Markdown artifact with the same action type is not the completion locator. Missing
completion state is rejected with `replay_not_complete`.

### Frozen Ledger and Artifact Core

Rust must reuse the persisted-ledger loader and frozen ledger verifier. In addition, replay core
must validate every artifact reference occurrence in ledger order and artifact-reference order:

1. require a closed `ArtifactRef`, safe run-local relative path, known artifact type, lowercase raw
   content hash, and a producing-commit hash;
2. require the producing-commit hash to name the commit containing that exact full artifact
   reference, including ID, type, path, content hash, metadata, and self-link;
3. allow an artifact ID to recur for distinct JSON/Markdown presentation artifacts, but reject a
   duplicate path or ambiguous duplicate full identity across commits;
4. reject absolute paths, traversal, non-normal components, symlinks in any component, non-files,
   cross-run paths, and paths escaping `runs/<run_id>/`;
5. hash final raw on-disk bytes and require an exact match with the ledger reference;
6. require every ledger-linked `.json` artifact used by core dependency or authority checks to be
   valid UTF-8, duplicate-key-free JSON before inspecting exact scalar values;
7. reject any ledger-linked artifact beneath `replay/`, `diagnostics/`, or `comparisons/`, because
   these derived views are outside provenance and must never be made authoritative retroactively.

Sidecar files are not independent replay inputs. A sidecar may be inspected only as a compatibility
cross-check against the authoritative ledger reference; it cannot repair or override a missing or
different ledger reference. Filesystem enumeration must not add artifacts to the verified
inventory.

For exact Python-bridge comparison, Rust computes `ledger_artifact_inventory_hash` as SHA-256 over
Python-compatible canonical JSON of an array in ledger order and artifact-reference order. Each
array member contains the containing `commit_hash` and the complete persisted `ArtifactRef` object,
including metadata. This digest is a bounded comparison value only; it is not a capability or
provenance replacement.

### Frozen Manifest and Dependency Core

Rust resolves the latest `ArtifactManifestWritten` commit at or before the requested tip, requiring
one JSON `report` artifact with ID `artifact-manifest`, canonical run-local path, exact self-link,
and raw bytes equal to the commit payload. The artifact parses as a closed `ArtifactManifest`.

The manifest must satisfy all of the following:

- `run_id` equals the request and `source_of_truth` equals `ledger`;
- every entry has a non-null lowercase content hash and producing-commit hash;
- entry paths are unique; repeated artifact IDs remain legal only for distinct paths;
- entries are in the writer's canonical ascending `(path, artifact_id)` order;
- evidence and presentation counts equal the flags derived from the entries;
- every entry exactly matches one ledger artifact reference, including metadata;
- the ordered entry set is exactly the artifact-reference set from commits preceding the manifest
  commit: no omitted ledger artifact, injected filesystem artifact, later artifact, or manifest
  self-reference is allowed;
- every entry's raw bytes have already passed the ledger artifact checks;
- `is_evidence` and `is_presentation` agree with the deterministic manifest classification derived
  from the exact matching `ArtifactRef`; neither flag is trusted merely because the manifest says
  so.

The representative completed fixture establishes that this exact-prefix rule is satisfiable: its
manifest contains 150 entries matching all 150 artifact references before the manifest commit,
while the complete ledger later contains 232 commits and 170 artifact references. These counts are
fixture observations, not protocol constants.

Rust also resolves the latest required output for each existing Python replay prerequisite:

- `FinalNucleusSelected` with an `id` payload;
- `ClaimTableBuilt` with a `claims` payload;
- `ManuscriptPlanBuilt` with a `sections` payload;
- `DraftSkeletonBuilt` with a `section_stubs` payload;
- `ArtifactManifestWritten` with an `artifacts` payload;
- `BranchOutcomesWritten` with a `branch_outcomes` payload;
- `ResearchObjectWritten` with a `final_nucleus` payload;
- `PaperSkeletonWritten` with a `paper_id` payload;
- `FinalAuditReportWritten` with a `checks` payload;
- `ReleaseGateDecided` with a `status` payload;
- `ExportReadinessReportWritten` with a `ready_for_polished_prose` payload.

Each required output must resolve through its commit to one unambiguous JSON artifact whose path,
self-link, raw hash, and bytes are valid. Rust does not recompute final-audit, release-gate, or
export-readiness policy in `replay.verify_core`; those remain full Python replay checks.

The latest claim table must reuse the closed persisted claim-table parsing already frozen for
`claim.resolve`: exact artifact/commit payload equality, unique safe claim IDs, closed claim and
evidence-link records, and valid label/section grammar. For dependency integrity:

- every claim evidence artifact ID must resolve to exactly one manifest entry;
- that entry must be structurally evidence-classified and must not be presentation;
- every supporting claim/evidence pair must have one matching `supports_label=true` evidence link;
- every supporting evidence link must name an existing claim and the same manifest dependency;
- duplicate, dangling, ambiguous, or contradictory supporting links are rejected.

These checks establish dependency integrity only. They do not re-run a proof, validate an
experiment, establish retrieval adequacy, or make a claim admissible. A fake proof or fake
synthetic artifact may remain structurally linked in `DevelopmentCompatibility` fixtures and may
also be hash-verified in `StrictProduction`; neither case constructs a strict evidence capability.
Strict evidence and claim admissibility remain owned by `evidence.validate_bundle` and
`claim.resolve` in separate request-local decisions.

### Frozen Authority Boundary

`replay.verify_core` must reject:

- any manifest entry that is both evidence and presentation, or any Markdown, LaTeX, PDF, replay,
  diagnostics, comparison, runtime-summary, or other presentation/derived artifact used as claim
  evidence;
- a claim dependency whose artifact is missing, ambiguous, unlinked, not structurally
  evidence-classified, or marked presentation;
- the labels `ExperimentVerified` or `RealDataExperimentVerified` anywhere in ledger-linked JSON
  run artifacts as exact JSON scalar values, because neither is constructible in the current MVP;
- a replay, diagnostics, comparison, manifest, runtime summary, manuscript, LaTeX, rendered PDF,
  release/readiness report, or other derived/presentation artifact represented as proof or
  experiment evidence;
- any request, manifest, artifact, or result field that claims replay itself creates evidence,
  scientific validation, human approval, novelty proof, accepted-paper status, or publication
  readiness.

`LeanVerified` and `SyntheticExperimentVerified` records may pass the structural dependency core,
but this operation never endorses those labels. Only separate strict bundle validation plus claim
resolution can decide their admissibility for one exact claim. Kernel mode therefore does not
change replay-core byte, dependency, or forbidden-authority decisions; both modes return literal
`authority_granted=false` and must have differential coverage.

### Frozen Result

An accepted result contains only:

```text
run_id
ledger_tip_hash
ledger_commit_count
ledger_artifact_count
ledger_artifact_inventory_hash
required_outputs_checked
manifest_artifact_id
manifest_producing_commit_hash
manifest_entry_count
manifest_inventory_hash
claims_checked
claim_evidence_links_checked
core_replay_valid = true
ledger_snapshot_stable = true
authority_boundary_valid = true
authority_granted = false
```

`manifest_inventory_hash` is SHA-256 over Python-compatible canonical JSON of the complete manifest
entry array in persisted order. Counts and inventory digests are exact bridge-comparison fields,
not verification evidence, provenance, reusable capability tokens, or permission to skip future
checks. `required_outputs_checked` counts all eleven listed prerequisite artifacts, including the
separately validated artifact manifest; eleven is the frozen inventory size, not a caller-supplied
value. Any core mismatch rejects the operation with an empty result; Rust does not synthesize a
partial `ReplayVerificationReport` or return a caller-trusted `ReplayStatus`.

The existing Python `replay_verify_run` remains the public full replay entry point during shadow
work. Its `ReplayVerified` status still means deterministic internal consistency only and does not
mean scientific validation or publication readiness. Optional JSON/Markdown replay report writing
remains Python-only, outside the kernel request, outside the ledger, and outside artifact manifests.

### Stable Replay-Core Diagnostics

New replay-specific diagnostics are:

- `replay_not_complete`
- `replay_not_latest`
- `replay_snapshot_changed`
- `replay_required_output_missing`
- `replay_required_output_invalid`
- `replay_manifest_missing`
- `replay_manifest_invalid`
- `replay_manifest_mismatch`
- `replay_dependency_missing`
- `replay_dependency_ambiguous`
- `replay_dependency_mismatch`
- `replay_authority_violation`

Existing protocol, ledger, artifact, hash, path, claim-record, and authority diagnostics remain
applicable and should be preferred for their precise lower-level causes. Diagnostics may name
bounded run-relative paths, artifact IDs, action types, claim IDs, expected categories, and observed
digests. They must not include raw artifact bodies, unbounded commit payloads, credentials, or
environment secrets.

## Current Luna Handoff: `replay.verify_core`

Luna owns only the bounded implementation below:

1. inventory the exact required commits, artifact references, manifest prefix, claim dependencies,
   and derived-report exclusions from successful, warning, failed-replay, and fake development
   fixtures before changing protocols;
2. add closed `replay.verify_core` request/result models with only the frozen fields, add the
   discriminator arms, bump the protocol minor version from `0.85.0` to `0.86.0`, and regenerate
   schemas/examples with `factori export-protocols`;
3. implement persisted current-tip verification, full-ledger validation, snapshot-stability checks,
   all-ledger-artifact verification, exact manifest-prefix comparison, required-output resolution,
   claim dependency checks, forbidden-label scanning, and literal non-authority in small reusable
   Rust functions;
4. compute the two canonical inventory digests exactly as frozen and add Python/Rust golden parity
   fixtures for them;
5. add a read-only Python shadow bridge that supplies only run ID/current tip and verifies every
   accepted identity, count, digest, literal `true` integrity flag, and literal
   `authority_granted=false` field;
6. add both-mode differential tests for representative completed runs and single-mutation tests for
   stale/current tip, concurrent append, ledger corruption, missing/tampered/symlinked/path-escaping
   artifacts, producer-link mismatch, duplicate paths, required-output removal, manifest
   count/order/path/metadata/prefix mutations, dangling or ambiguous claim dependencies,
   presentation evidence, forbidden labels, and ledger-linked replay/diagnostics outputs;
7. prove the Rust operation and Python bridge do not change ledger bytes/count/tip, artifact bytes,
   manifest bytes, sidecars, or replay-report presence;
8. run protocol export/version/currentness and example validation, Rust format/Clippy/unit and
   integration tests, focused Python/Rust parity, Ruff, and the complete Python suite before final
   Sol review.

Luna must stop and return to Sol if the current writer cannot satisfy the exact manifest-prefix
rule, if a required output cannot be selected without trusting filesystem discovery, if supporting
claim dependencies cannot be resolved unambiguously from persisted bytes, if Python/Rust canonical
inventory digests differ, if a valid fixture requires accepting `ExperimentVerified` or
`RealDataExperimentVerified`, or if implementation would require changing persistence, report
writing, evidence-label rules, or ledger ordering. Luna must not port the full replay report,
recompute release policy, add report writes to Rust, create an authority token, begin mutating
operations, or claim replay establishes scientific or publication validity.

## Sol Review of Protocol 0.86.0: Test Matrix Required

Sol reviewed Luna commit `f4b63a3` and corrected the bounded implementation in `3648029`. The
review found that the Python bridge compared only the request identity and authority booleans
rather than every frozen count and digest; supporting links could be contradictory, extraneous, or
inconsistent with manifest types and evidence roles; accepted response validation did not enforce
the two frozen constants or positive required counts; manifest location and artifact-byte snapshot
stability were underchecked; and forbidden true authority assertions in artifact metadata and JSON
objects were not rejected. Those implementation defects are corrected without widening the
operation, changing persistence, or granting authority. Protocol `0.86.0` remains the selected
version, with the already-frozen result constants represented exactly in its generated schemas.

The representative writer-produced complete run now passes through the bridge in both modes with
exact identity, count, and digest parity, literal non-authority, and no ledger, artifact, or replay
report mutation. Stale-tip rejection, bridge mismatch rejection, protocol currentness and examples,
Rust format/Clippy/tests, focused Python protocol/replay tests, and Ruff also pass.

Cutover gates 6 and 9 remain open because the correction handoff's adversarial differential matrix
is only partially implemented. Luna added a reusable writer-produced fixture and 17 replay-core
tests covering both modes, stale and bridged-result mismatches, read-only behavior, artifact and
manifest/claim tampering, missing output, ledger corruption, symlink and derived-path inputs, and
forbidden authority assertions. The suite still does not prove all enumerated single mutations or
concurrent snapshot changes fail closed in both modes. Gate 10 (Sol review) remains open pending
that matrix. Luna's next bounded handoff is therefore:

1. extend the reusable writer-produced replay fixture and deterministic mutation/resealing helpers
   without altering production persistence behavior;
2. cover incomplete and stale snapshots, concurrent append and artifact-byte changes, ledger
   corruption, missing/tampered/symlinked/path-escaping artifacts, producer-link mismatches,
   duplicate paths and identities, and required-output removal;
3. cover manifest count, ordering, path, metadata, prefix, and classification mutations;
4. cover missing, ambiguous, duplicate, contradictory, type-mismatched, role-mismatched, and
   extraneous claim/evidence dependencies, presentation or derived evidence, forbidden labels and
   true authority assertions, and ledger-linked replay/diagnostics/comparison paths;
5. require the same accepted/rejected result and stable diagnostic code in
   `DevelopmentCompatibility` and `StrictProduction`, while rechecking exact bridge digests and
   complete nonmutation;
6. add the remaining concurrent-snapshot and semantic mutation cases, then rerun protocol
   currentness/version/examples, Rust format/Clippy/unit and integration tests, focused parity,
   Ruff, and the complete Python suite before returning to Sol.

This handoff is test-only unless a mutation fixture exposes a new semantic mismatch. Luna must not
weaken a validator to make a mutated fixture pass, change the frozen request/result shape, port the
full Python replay report, begin mutating operations, or claim cutover before final Sol review.

## Sol Review of Luna `5240865`: Semantic Matrix Still Open

Sol accepts the reusable fixture, both-mode parameterization, immutable-byte corruption cases,
stale-tip and bridge-mismatch checks, authority-assertion rejection, symlink/path rejection, and
complete nonmutation check as useful regression coverage. Luna changed no production code. The
focused 17-test replay suite and the reported complete 1,523-test Python run are green, as are Rust,
Ruff, and protocol checks.

This is not yet the frozen adversarial matrix. The `manifest_tamper` and `claim_tamper` cases append
unhashed bytes without resealing the affected commit and its descendants, so both stop at the
outer artifact-hash check; they do not exercise manifest ordering/count/path/metadata/prefix or
claim-link dependency validation. Likewise, stale-tip coverage is not a deterministic concurrent
append test, and no test changes artifact bytes between the kernel's first and final snapshot
checks. Producer mismatch, duplicate identity/path, path escape, required-output selection,
forbidden-label, and the detailed dependency mutations remain uncovered at their intended semantic
layer.

Cutover gates 6, 9, and 10 therefore remain open. Luna's final test-only correction must add a
suffix-resealing fixture helper (including manifest-entry updates when an earlier artifact changes),
then exercise each remaining mutation so the expected diagnostic proves the intended validator was
reached. Concurrent snapshot tests must be deterministic and must prove both ledger append and
artifact-byte replacement are caught by the final stability pass. Every accepted and rejected case
must remain mode-identical and read-only. After focused checks and the complete suite return green,
Sol can perform the final operation-level cutover review.

## Final Sol Review of `replay.verify_core`: Approved

Sol reviewed Luna commit `ad6e53e`. The final test-only correction adds suffix resealing for every
descendant commit and updates the manifest prefix when an earlier artifact changes, so manifest and
claim mutations reach their intended semantic validators rather than stopping at the outer hash
check. The matrix now covers incomplete and stale snapshots, exact bridge comparison and
nonmutation, ledger corruption, missing/tampered/symlinked/path-escaping artifacts, producer and
duplicate-path failures, ambiguous and missing required outputs, canonical manifest location,
manifest count/order/path/metadata/prefix/classification changes, all frozen claim-link mismatch
classes, presentation evidence, forbidden labels and authority assertions, and ledger-linked
replay/diagnostics/comparison paths in both modes.

Concurrent stability is exercised deterministically. The tests wait until Rust has opened a final
large sentinel artifact, then either append a valid commit or replace an artifact already checked
in the first pass. Both modes reject with `replay_snapshot_changed`, proving the final ledger and
artifact stability passes are reached. No production source changed in the correction.

The completed validation matrix is 73 focused replay-core tests, the complete 1,579-test Python
suite, Ruff, protocol `0.86.0` currentness and all 51 examples, Rust formatting and Clippy, and all
16 Rust tests. Cutover gates 6, 9, and 10 are closed for this operation. `replay.verify_core` is
approved only for the bounded read-only mechanical integrity decision frozen above. It creates no
evidence, validates no scientific claim, grants no human or publication authority, writes no
report, and does not replace the full Python replay policy layer. Python fallback remains explicit
until a call site deliberately cuts over; silent fallback remains forbidden.

This approval completes the initial read-only kernel operation set. No mutating operation is
approved by implication.

## `artifact.persist` Scope Decision

The first mutating kernel operation is a fail-closed atomic persistence of one canonical JSON
artifact into an already initialized run directory. This first slice is intentionally JSON-only.
Markdown, LaTeX, bibliography, arbitrary text, and binary persistence remain Python-owned until a
later reviewed protocol expansion. The operation writes no ledger row and no producing-commit
sidecar, constructs no evidence capability, and returns only an unlinked `ArtifactRef`.

### Frozen Request and Content

The caller supplies only:

```text
run_id
artifact_id
artifact_type
json_value
metadata
filename_stem_optional
overwrite_policy = FailIfExists
```

`run_id`, `artifact_id`, and an optional filename stem use the existing safe-segment grammar.
`artifact_type` is one current closed `ArtifactType`; Rust derives its directory from the frozen
type-to-directory mapping. The caller cannot supply a path, extension, format label, content hash,
producing commit, temporary-file name, ledger parent, action type, or any separate evidence-label
or authority-control field. The destination is exactly
`runs/<run_id>/<artifact-type-directory>/<filename-stem-or-artifact-id>.json`.

`json_value` is one duplicate-key-free protocol JSON value and is serialized with the frozen
Python-compatible canonical serializer plus exactly one trailing LF byte. The final artifact is
limited to 12 MiB after canonical serialization; the existing 16 MiB transport limit remains in
force. Non-finite or unrepresentable numbers, unsafe strings, and canonicalization failures are
rejected before filesystem mutation.

`json_value` is persisted data, not an authority-control surface. It may contain domain records and
existing labels required by public schemas; Rust must not reinterpret or upgrade them during this
operation. Successful persistence alone grants nothing.

Caller metadata is a JSON object with at most 64 entries, UTF-8 keys of at most 128 bytes, and at
most 64 KiB of Python-compatible canonical JSON before kernel-owned fields are inserted. Rust
inserts `format="json"` and defaults `is_verification_evidence=false`; the caller cannot override
either field, supply a producing-commit field, set an equivalent evidence flag, or assert scientific
validation, human approval, novelty proof, accepted-paper status, or publication readiness.
Metadata and the returned reference remain context only. An evidence-looking role never creates a
capability in this operation.

`FailIfExists` is the only accepted overwrite policy in this slice. A regular file, directory,
symlink, dangling symlink, or other filesystem object at the final path is rejected. Replacement
with an expected prior hash remains required by the general artifact contract but is deferred to a
separate Sol review; Luna must not implement blind overwrite or an ad hoc check-then-replace race.

### Frozen Filesystem and Atomicity Rules

The configured project root, `runs/<run_id>`, and the exact artifact-type directory must already
exist as real directories. No component may be a symlink, and canonical resolution must stay below
the requested run. `artifact.persist` does not initialize a run or create the standard directory
tree. A pre-existing producing-commit sidecar for the destination is an inconsistent target and is
also rejected.

After all validation and byte construction, Rust must:

1. create a unique same-directory temporary regular file with exclusive creation;
2. write all canonical bytes, flush userspace buffers, and fsync the temporary file;
3. publish without overwriting an existing destination, using an atomic no-clobber primitive;
4. remove the temporary name after successful publication;
5. fsync the containing directory where the platform supports it;
6. verify the final regular file's raw SHA-256 and length, then return the exact unlinked artifact
   reference only after those postconditions pass.

Pre-publication failure must leave no final artifact and must clean the temporary file best-effort.
The implementation must not use a check followed by ordinary replacing rename for no-clobber
semantics. Linux hard-link publication from the same-directory temporary file is acceptable when
followed by temporary unlink and directory fsync. A cleanup failure after successful publication
returns the valid result with one bounded warning diagnostic rather than denying that mutation
occurred. A supported directory-fsync failure or failed final postcondition after publication
returns an empty error result with `mutation_performed=true` and a stable post-publication
diagnostic; the caller must stop and inspect the exact destination rather than retry blindly.
Cross-filesystem/SQLite transaction atomicity is outside this operation because it writes neither
outside the destination directory nor to SQLite.

### Frozen Result and Response Semantics

An accepted result contains only:

```text
artifact
bytes_written
created = true
linked_to_ledger = false
authority_granted = false
```

The returned `artifact` has the requested ID and type, derived canonical path, SHA-256 of exact
on-disk bytes, `producing_commit_hash=null`, and normalized metadata. `bytes_written` is the exact
raw file length including the trailing LF. Accepted responses set `mutation_performed=true`.
Rejected and pre-publication error responses set it to false, return an empty result, and leave the
destination absent. A post-publication durability or postcondition error also returns an empty
result but sets `mutation_performed=true`. Existing response validators must be changed narrowly:
all read-only operations still require `mutation_performed=false`; `artifact.persist` requires true
on acceptance and permits true on error only with a frozen post-publication diagnostic.

The Python bridge computes the expected canonical bytes, path, metadata, hash, and length before
invoking Rust; it never calls the Python writer as a shadow mutation. After acceptance it compares
every result field, hashes the final bytes independently, proves the ledger bytes/count/tip and all
pre-existing run artifacts are unchanged, and proves no sidecar was created. There is no silent
Python fallback.

### Stable Persist Diagnostics

New operation-specific diagnostics are:

- `artifact_persist_run_missing`
- `artifact_persist_directory_invalid`
- `artifact_persist_target_exists`
- `artifact_persist_payload_invalid`
- `artifact_persist_size_exceeded`
- `artifact_persist_temp_write_failed`
- `artifact_persist_publish_failed`
- `artifact_persist_temp_cleanup_warning`
- `artifact_persist_durability_uncertain`
- `artifact_persist_postcondition_failed`

Existing protocol, canonicalization, path, and authority diagnostics remain applicable when more
precise. Diagnostics may include the bounded run-relative destination and expected/observed sizes
or hashes, but never raw content, unbounded metadata, temporary random names, or environment data.

## Luna Handoff for `artifact.persist` (Completed)

Luna owns only this JSON-only slice:

1. add closed request/content/result schemas, discriminator arms, exact response mutation semantics,
   and a minor protocol bump from `0.86.0` to `0.87.0`; regenerate schemas and examples rather than
   editing generated JSON Schemas;
2. implement pre-mutation canonicalization, size, metadata-authority, safe-name, derived-path,
   existing-target/sidecar, directory, and symlink checks in small Rust functions;
3. implement exclusive same-directory temporary creation, complete write/flush/file-fsync,
   atomic no-clobber publication, directory fsync, cleanup, and final byte/hash/length verification;
4. add the Python expectation bridge without a Python write fallback and compare every frozen
   result and non-authority field;
5. add both-mode byte-parity tests for nested JSON, Unicode/control characters, floats and negative
   zero, empty values, metadata normalization, filename stems, and every artifact type;
6. add rejection and race tests for unsafe names, wrong content shape, size limits, authority
   metadata, missing/symlinked directories, existing files/directories/symlinks/sidecars, concurrent
   same-target creation, and malformed responses;
7. add deterministic fault injection around create, write, flush, file fsync, publish, directory
   fsync, cleanup, and postcondition verification, proving final/temp-file state and exact
   pre- versus post-publication `mutation_performed` semantics at each boundary;
8. prove accepted persistence changes only the one final file, leaves ledger bytes/count/tip and all
   pre-existing artifacts unchanged, creates no sidecar, and returns no authority;
9. run protocol compatibility/version/currentness/examples, Rust format/Clippy/unit/integration and
   focused Python parity, Ruff, and the complete Python suite before returning to Sol.

Luna must stop and return to Sol if portable atomic no-clobber publication cannot satisfy the
frozen semantics, if a failure can publish a final artifact while reporting
`mutation_performed=false`, if canonical bytes differ from Python, if the operation needs to create
run directories, if response-envelope mutation rules cannot remain operation-specific, or if the
implementation would touch SQLite, create a sidecar, accept overwrite, support non-JSON content,
or grant any evidence or publication authority. Luna must not begin `ledger.append`,
`artifact.link`, or `persistence.commit_bundle` in this handoff.

## Final Sol Review of `artifact.persist`: Approved

Sol reviewed Luna commit `515c1f6` and hardened the operation in `83a570c`. The correction maps an
atomic-publication race loser to `artifact_persist_target_exists`, validates canonical request and
metadata size in Python as well as Rust, closes accepted and post-publication response diagnostics,
validates returned paths, types, producer state, JSON metadata, and non-authority in both protocol
validators, and makes the bridge prove rejected calls leave the complete run tree unchanged.

The final matrix covers every current artifact type, Python/Rust canonical bytes for nested JSON,
Unicode, control characters, floating-point values and negative zero, optional stems, normalized
metadata, existing files, directories, symlinks, dangling symlinks and sidecars, missing and
symlinked directories, unsafe names, closed overwrite policy, content and metadata bounds,
authority assertions, same-target concurrency, and malformed accepted/rejected/error envelopes.
Rust-only deterministic faults cover exclusive creation, write, flush, file fsync, publication,
temporary cleanup, directory fsync, and final postconditions with exact mutation flags.

The focused result is 26 artifact-persistence Python tests plus the corrected schema-export test,
19 Rust unit tests, 51 valid protocol examples, Ruff, and clean diffs. The complete Python run
reached 1,602 passes with one stale schema-export expectation; that sole failure was corrected and
rerun successfully at the user's direction without repeating the full 17-minute suite.

`artifact.persist` is approved only as the frozen unlinked JSON file mutation. It does not initialize
a run, overwrite a target, write SQLite, create a producer sidecar, construct evidence, authorize a
label, or cut over an existing Python persistence call site. No later mutation is approved by this
review.

## `ledger.append` Scope Decision

The next mutating kernel operation is a transactionally serialized append of one artifact-free,
non-root commit to an already existing, valid SQLite ledger. This slice is deliberately unable to
consume an `ArtifactRef`: artifact-bearing commits, producer self-links, sidecar persistence, and
the complete artifact/commit/link sequence remain deferred. The restriction lets Rust establish
SQLite schema, chain, current-tip, hash, concurrency, rollback, durability, and bridge parity
without creating an intermediate ledger-reference/sidecar inconsistency.

This operation is mechanical provenance persistence only. It does not authorize a stage rerun,
interpret a commit payload as scientific truth, establish stage completion, grant evidence,
approve a claim, or make a run ready for human review or publication.

### Frozen Request

The caller supplies only:

```text
run_id
expected_tip_hash
action_type
payload
candidate_id_optional
timestamp
```

`run_id` and an optional candidate ID use the existing safe-segment grammar.
`expected_tip_hash` is one lowercase SHA-256 digest and becomes the new commit's parent. The caller
cannot supply a root/null parent, commit hash, row ID, commit count, artifact reference, producing
commit, sidecar, database path, SQL, transaction mode, or retry policy.

`action_type` is one current closed `ControllerActionType` except `InitRun`. Root initialization is
out of scope because this operation must open an existing non-empty ledger without creating the
database, table, triggers, run directory, or root commit. Rust must reuse the already audited
closed action-type validator rather than accept arbitrary strings or add a second divergent list.

`payload` is one duplicate-key-free JSON object, serialized with Python-compatible canonical JSON,
and is limited to 4 MiB after serialization. It is persisted provenance data, not an authority
control surface. Existing labels and stage records may be recorded, but `ledger.append` does not
endorse, upgrade, or turn them into capabilities. The operation returns literal
`authority_granted=false` in both modes.

`timestamp` is caller-supplied so the Python `Clock` seam and deterministic fixtures remain
authoritative. It must be an ASCII UTC timestamp of the form `YYYY-MM-DDTHH:MM:SSZ` or
`YYYY-MM-DDTHH:MM:SS.ffffffZ`, with one through six fractional digits when present, at most 32
bytes, and a real calendar/time value. Rust persists the exact validated string; it does not read
the wall clock. The optional candidate ID is persisted exactly or as SQL `NULL`.

Both kernel modes have identical byte, chain, transaction, and result behavior for this operation.
Mode never changes the commit hash and grants no authority.

### Frozen Existing-Ledger Preconditions

The configured root, `runs`, and `runs/<run_id>` must already exist as real non-symlink directories.
`runs/<run_id>/ledger.sqlite` must already exist as a real regular file below that run. Rust must
not create or initialize any path. A symlink at any component, a missing or empty ledger, a corrupt
database, a ledger containing another run ID, or a pre-existing `ledger.sqlite-journal`,
`ledger.sqlite-wal`, or `ledger.sqlite-shm` object rejects before mutation.

Rust configures foreign keys, a zero busy timeout, the existing journal mode unchanged, and
synchronous durability at least `FULL`, then begins one immediate write transaction. Inside that
same transaction it validates the expected Python ledger schema: the exact required `commits`
columns and primary key, the foreign-key parent relation, and both append-only no-update and
no-delete triggers. SQLite integrity and foreign-key checks must also pass before insertion.

Inside that same transaction Rust reloads and validates every commit in row insertion order using
the already approved chain rules. The ledger must be a single non-empty linear history belonging
only to `run_id`, with one current insertion-order tip, no duplicate/fork/broken parent, valid closed
action types, exact Python canonical payload and artifact-reference bytes, and valid commit hashes.
Every pre-existing row may contain its already persisted artifact references; this slice merely
forbids artifact references on the new request. The current tip must equal `expected_tip_hash`.

A busy database rejects with no wait and no mutation. A caller may retry only after rereading and
revalidating the ledger; the kernel never silently changes the expected parent. A concurrent caller
using the same expected tip either loses at `BEGIN IMMEDIATE` with `ledger_append_busy` or, after
the winner commits and a deliberate new invocation begins, rejects with
`ledger_append_tip_mismatch`. It must never create a fork.

### Frozen Hash and Row

Rust constructs the new commit with:

```text
parent_hash = expected_tip_hash
run_id = requested run_id
candidate_id = candidate_id_optional
action_type = requested closed action type
payload = requested JSON object
artifact_refs = []
timestamp = requested timestamp
```

The commit hash is SHA-256 over the existing Python `commit_hash_payload` canonical JSON contract.
There are no self-link placeholders because the artifact list is empty. SQLite stores
`payload_json` as exact Python-compatible canonical JSON and `artifact_refs_json` as the two bytes
`[]`; it stores the validated action enum value and timestamp exactly.

Rust performs one parameterized `INSERT`; caller values are never interpolated into SQL. An insert,
constraint, trigger, serialization, or pre-commit verification failure rolls the transaction back
and returns `mutation_performed=false`. Before commit, Rust reads the inserted row back inside the
transaction and proves exact field, canonical-byte, and computed-hash equality.

After `COMMIT` returns success, Rust closes the writer, reopens the ledger read-only, reruns the
complete chain validation, and proves the prior prefix is unchanged, the count increased by exactly
one, the inserted row is the sole new row, and its hash is the unique current tip. No SQLite
auxiliary file may remain. This verification happens before an accepted response.

If SQLite reports an error while committing, the kernel treats durability as ambiguous: it returns
an empty `error` result with `mutation_performed=true` and requires caller inspection rather than a
blind retry. A failure after a successful commit likewise returns an empty postcondition error with
`mutation_performed=true`. Rust must never report false after the transaction may have committed.

### Frozen Result and Response Semantics

An accepted result contains only:

```text
commit
previous_tip_hash
new_tip_hash
commit_count_before
commit_count_after
appended = true
linked_artifact_count = 0
authority_granted = false
```

`commit` is the exact new public `LedgerCommit`, with `parent_hash=previous_tip_hash`,
`commit_hash=new_tip_hash`, requested identity/action/payload/timestamp fields, and an empty
`artifact_refs` array. `commit_count_before` is positive and `commit_count_after` equals it plus
one. An accepted response has no diagnostics and sets `mutation_performed=true`.

A rejected or pre-commit error has an empty result, one stable diagnostic,
`mutation_performed=false`, and byte-identical ledger/run state. A commit-uncertain or
postcondition error has an empty result, one corresponding stable diagnostic, and
`mutation_performed=true`. Existing response validators must remain operation-specific: read-only
operations remain false; accepted `artifact.persist` and `ledger.append` are true; only the frozen
post-publication codes permit true on an error.

Stable new diagnostics are:

- `ledger_append_run_missing`
- `ledger_append_directory_invalid`
- `ledger_append_ledger_invalid`
- `ledger_append_root_unsupported`
- `ledger_append_payload_invalid`
- `ledger_append_size_exceeded`
- `ledger_append_tip_mismatch`
- `ledger_append_busy`
- `ledger_append_insert_failed`
- `ledger_append_commit_uncertain`
- `ledger_append_postcondition_failed`

Existing lower-level ledger hash, parent, action, canonicalization, and protocol diagnostics may be
used when they identify the exact precondition failure more precisely. Diagnostics may contain
bounded run IDs, row numbers, action types, expected/observed hashes, and SQLite error categories.
They must not contain SQL text, full payloads, raw rows, host paths, environment data, or secrets.

### Frozen Python Bridge and Cutover Boundary

Before invoking Rust, the Python bridge validates the request, snapshots all run paths and raw
bytes, opens the ledger read-only, validates its complete current history, proves the expected tip,
and computes the exact expected commit and hash with `compute_commit_hash`. It invokes Rust once and
has no Python append fallback.

On acceptance the bridge compares every result field, reloads the ledger read-only, proves the
complete prior commit prefix is unchanged and exactly one expected row was appended, reruns normal
ledger validation, and proves every non-ledger run path and byte is unchanged. It also proves no
sidecar, artifact, report, or persistent SQLite auxiliary file appeared. On a false-mutation
rejection it proves the raw ledger file and complete run tree are byte-identical. On a true-mutation
error it raises an inspection-required bridge error and never retries automatically.

This is development shadow mutation only. No existing stage, persistence helper, CLI command, or
rerun path cuts over in this slice. Python remains the production ledger writer until a later Sol
review approves artifact-bearing commits, producer linking, crash recovery, and the complete
`persistence.commit_bundle` orchestration.

## Current Luna Handoff: Artifact-Free `ledger.append`

Luna owns only this transaction and parity slice:

1. add the closed request/result models and discriminator arms, narrow operation-specific response
   mutation rules, bump the protocol minor version from `0.87.0` to `0.88.0`, and regenerate all
   schemas/examples;
2. add strict request validation for safe IDs, non-root closed action type, object payload,
   canonical 4 MiB bound, exact UTC timestamp, and the absence of every artifact/path/SQL/control
   field;
3. validate the existing real-directory path, regular non-symlink ledger, absent SQLite auxiliary
   objects, exact table/foreign-key/trigger schema, SQLite integrity, and complete current chain
   before mutation;
4. implement `BEGIN IMMEDIATE`, zero-wait concurrency, inside-transaction tip revalidation,
   Python-compatible commit hashing, parameterized insert, row readback, rollback, commit, close,
   read-only full-chain postconditions, and exact mutation flags;
5. add a Python no-fallback bridge that computes the expected commit before Rust, compares every
   result field, proves exact prior-prefix preservation and one-row extension, and snapshots all
   non-ledger state and SQLite auxiliary paths;
6. add Python/Rust parity for all allowed action types except `InitRun`, payload nesting/Unicode/
   floats/empty objects, candidate presence/absence, timestamps with and without fractional digits,
   canonical stored JSON, commit hashes, counts, tips, and both modes;
7. add rejection tests for unknown fields, unsafe IDs, null/wrong parents, `InitRun`, non-object or
   oversized payloads, invalid timestamps, missing/symlinked/corrupt/empty/wrong-run ledgers,
   schema/trigger/foreign-key damage, broken hashes/parents/forks, stale tips, persistent auxiliary
   files, locked databases, constraint/insert failures, and malformed responses;
8. add deterministic transaction fault injection at open, begin, validate, insert, readback,
   rollback, commit, reopen, and postcondition boundaries; prove exact raw bytes and semantic state
   for every false-mutation outcome and inspection-required true-mutation outcome;
9. add deterministic concurrency tests for a held immediate lock, a stale expected tip after a
   winner commits, and multiple simultaneous callers, proving one linear extension and no fork;
10. run protocol compatibility/version/currentness/examples, Rust format/Clippy/unit/integration,
    focused Python/Rust parity, Ruff, and the complete Python suite before returning to Sol.

Luna must stop and return to Sol if Rust cannot match Python commit hashes and canonical stored JSON,
if SQLite can commit while an error reports `mutation_performed=false`, if a race can create a fork,
if validation requires creating or repairing schema/run state, if existing ledgers require a
different transaction/journal contract, or if the slice would need artifact references, sidecars,
root initialization, stage cutover, rerun authorization, evidence construction, or payload-policy
interpretation. Luna must not begin `artifact.link`, expand `ledger.append` to artifact-bearing
commits, implement `persistence.commit_bundle`, or cut over a production call site in this handoff.
