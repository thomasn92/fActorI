# fActorI Protocol Contracts

This directory contains language-neutral developer contracts generated from the repository's
existing Pydantic models. They are intended for Python tools, future Rust crates, future servers,
and coding agents that need a stable boundary without importing the Python runtime.

Protocol files are developer interfaces only. They are not run provenance, scientific evidence,
verification evidence, or a replacement for the append-only research ledger.

## Layout

- `version.json`: protocol metadata and explicit semantic version.
- `jsonschema/*.schema.json`: JSON Schema Draft 2020-12 contracts.
- `examples/*.example.json`: small deterministic payload examples.
- `examples/README.md`: example-use and evidence-boundary notes.

Public protocol names are stable even where internal Python names differ. Current aliases include:

- `ArtifactRecord` generated from `ArtifactRef`;
- `StageResult` generated from `PipelineStageResult`;
- `ReviewerReport` generated from `StageBReviewerReport`;
- `ProofVerificationResult` generated from `FakeProofResult`;
- `ExperimentRunResult` generated from `FakeExperimentResult`.

The generated schema records the qualified Python source model in
`x-factori-source-model`. Fake proof and experiment result schemas retain their explicit `fake`
field and do not imply real scientific verification.

## Update And Check

```bash
uv run factori export-protocols
uv run factori export-protocols --check
```

Use `--output-dir` to export an isolated copy for another toolchain. Check mode is read-only and
fails if a schema, version file, or example is missing or stale.

Consumers should pin `protocol_version` from `version.json`, validate payloads at process
boundaries, and treat version changes as interface changes requiring compatibility review.
