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
- `compatibility.md`: conservative schema-change classification policy.
- `versioning.md`: semantic version bump rules for protocol changes.
- `server-readiness.md`: current future-server contract boundary.

Public protocol names are stable even where internal Python names differ. Current aliases include:

- `ArtifactRecord` generated from `ArtifactRef`;
- `StageResult` generated from `PipelineStageResult`;
- `ReviewerReport` generated from `StageBReviewerReport`;
- `ProofVerificationResult` generated from the provider-neutral proof result model;
- `ExperimentRunResult` generated from the provider-neutral synthetic experiment result model.
- `RunSummary` generated from `LedgerSummary`;
- `PipelineStagePlan` generated from `PlannedStage`;
- `LLMReviewerPromptContract` generated from `ReviewerPromptContract`;
- `LLMReviewerParseReport` generated from `LLMReviewerParseResult`;
- `ProseSectionContract`, `ProsePromptContract`, `ProseGenerationRequest`,
  `ProseGenerationParseResult`, and `ProseSafetyReport` generated from one-section prose adapter
  models;
- `ManuscriptDraftingPlan`, `SectionDraftingTask`, `SectionDraftingResult`,
  `CompleteMarkdownDraft`, `ManuscriptDraftingReport`, and `ManuscriptAssemblyReport` generated
  from manuscript drafting models;
- `CitationRecord`, `CitationRegistry`, `BibliographyEntry`, `CitationUsage`,
  `CitationSafetyReport`, `LiteratureGapStatement`, `LiteraturePositioningContract`, and
  `LiteraturePositioningReport` generated from citation-safe manuscript/literature models;
- `LatexExportContract`, `LatexSourceMap`, `LatexSafetyReport`, `LatexRenderConfig`,
  `LatexRenderResult`, `LatexCompileCheckReport`, and `LatexExportResult` generated from
  presentation/export models;
- `PaperCriticFinding`, `PaperCriticReport`, `PaperReleaseReadinessPreview`,
  `SectionRevisionPlan`, `PaperRevisionPlan`, `PaperRevisionPatch`, `RevisionSafetyReport`, and
  `PaperRevisionResult` generated from generated-paper critique and deterministic fake revision
  models;
- `FullPaperGenerationConfig`, `FullPaperGenerationStep`, `FullPaperArtifactBundle`,
  `FullPaperGenerationReport`, and `FullPaperGenerationResult` generated from end-to-end
  non-evidence paper-package orchestration models;
- `FullPaperReleaseGateConfig`, `FullPaperReleaseCheck`, `FullPaperReleaseFinding`,
  `FullPaperBundleCompletenessReport`, `FullPaperEvidenceBoundaryReport`,
  `FullPaperReadinessDecision`, and `FullPaperReleaseReport` generated from the human-review-only
  generated-paper bundle readiness gate;
- `EvidenceType` generated from `ArtifactType`;
- `ClaimLabel` generated from `VerificationLabel`;
- `ReleaseStatus` generated from `ReleaseGateStatus`.

The generated schema records the qualified Python source model in
`x-factori-source-model`. Fake proof and experiment schemas retain explicit `fake` fields where
they are still exported or embedded. Provider-neutral proof and synthetic experiment result schemas
are only evidence when the owning run links validated proof or experiment artifacts; protocol
examples are not scientific evidence.
Schema definitions are grouped internally under `factori/schemas/`, but generated protocols keep
the stable `factori.schemas.<ModelName>` source-model path for compatibility.

## Update And Check

```bash
uv run factori export-protocols
uv run factori export-protocols --check
uv run factori validate-protocol-examples
```

Use `--output-dir` to export an isolated copy for another toolchain. Check mode is read-only and
fails if a schema, version file, or example is missing or stale.

Consumers should pin `protocol_version` from `version.json`, validate payloads at process
boundaries, and treat version changes as interface changes requiring compatibility review.

Compare two exported versions with:

```bash
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema --new-dir path/to/new/jsonschema
```

See [`compatibility.md`](compatibility.md) for the exact breaking, non-breaking, documentation, and
unknown-change policy. Compatibility checking is conservative and read-only.

Check protocol version movement with:

```bash
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --old-version 0.1.0 \
  --new-version 0.11.0
```

See [`versioning.md`](versioning.md) for MAJOR/MINOR/PATCH rules and
[`server-readiness.md`](server-readiness.md) for the future server contract boundary.
