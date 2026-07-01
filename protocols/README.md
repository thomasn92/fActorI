# fActorI Protocol Contracts

This directory contains language-neutral developer contracts generated from the repository's
existing Pydantic models. They are intended for Python tools, future Rust crates, future servers,
and coding agents that need a stable boundary without importing the Python runtime.

Protocol files are developer interfaces only. They are not run provenance, scientific evidence,
verification evidence, or a replacement for the append-only research ledger.

Protocol `0.36.0` adds deterministic gap-strategy options, append-only diversification reports and
indexes, strategy provenance on autonomous plan/execution records, and strategy-aware exhaustion
history. Diversification schedules local-only alternatives and creates no evidence, validation, or
publication readiness.

Protocol `0.35.0` adds stable autonomous gap/spec fingerprint fields, gap-attempt history records,
planned-spec de-duplication indexes, and attempt-aware execution/loop report fields. These workflow
contracts prevent duplicate spec churn and classify exhausted automation gaps without hiding them,
creating evidence, or changing publication readiness.

Protocol `0.34.0` adds autonomous loop decision, iteration, run report, and immutable index
contracts. The loop controller orchestrates deterministic planning, safe plan execution,
planned-spec execution, scoped artifact intake, evidence-map rebuilds, manuscript refreshes, and
safety rechecks. Loop reports are workflow context only and do not create evidence, validation, or
publication readiness.

Protocol `0.33.0` adds planned-spec execution item, report, and immutable index contracts for
gated deterministic local execution of planned proof, experiment, and retrieval specs. These
execution reports are workflow context only; local synthetic experiments remain bounded to mapped
claims, proof plans are not formal verification, fixture-backed proof artifacts require explicit
passed-checker scope, retrieval expansion is local/fixture-only, and publication readiness remains
false.

Protocol `0.32.0` adds autonomous plan execution action, report, immutable index, planned
experiment, proof-obligation, and retrieval-expansion request contracts. Execution artifacts
perform or schedule bounded deterministic workflow actions only; planned specs are not completed
experiments, verified proofs, retrieved sources, scientific validation, or publication readiness.

Protocol `0.31.0` adds autonomous evidence-gap plan and plan-item contracts. These planner
contracts schedule or recommend next automatic actions only; they do not create proof,
experiment evidence, novelty validation, correctness validation, human approval, or publication
readiness.

Protocol `0.30.0` adds structured reviewer change requests, immutable reconciliation-cycle index
entries, and derived multi-cycle reconciliation indexes. These workflow contracts do not create
evidence, scientific validation, approval, or publication readiness.

Protocol `0.29.0` adds deterministic human-review reconciliation item and report contracts.
Reconciliation records safe manuscript revisions, rejected authority requests, and deferred
evidence work without creating proof, experiment evidence, scientific validation, human approval,
or publication readiness.

## Layout

- `version.json`: protocol metadata and explicit semantic version.
- `jsonschema/*.schema.json`: JSON Schema Draft 2020-12 contracts.
- `examples/*.example.json`: small deterministic payload examples.
- `examples/README.md`: example-use and evidence-boundary notes.
- `compatibility.md`: conservative schema-change classification policy.
- `versioning.md`: semantic version bump rules for protocol changes.
- `server-readiness.md`: current future-server contract boundary.

`full-paper-golden-bundle.example.json` pins the structural 24-artifact paper bundle against the
existing `FullPaperArtifactBundle` schema. It adds no schema by itself; the current protocol
version is `0.21.0`.

Protocol `0.21.0` adds source-aware missing-citation repair fields to revision safety and
safe-repair reports. The fields record accepted-registry citation insertion, boundary downgrades,
sentence removals, unresolved repairs, and rejected-source safety flags without creating evidence
or publication readiness. Protocol `0.20.0` adds gated source relevance adjudication fields,
LLM budget/accounting fields
for source relevance calls, and a `SourceRelevanceAdjudication` contract. Source relevance
adjudication judges bounded background-context fit only; deterministic code still controls source
metadata, duplicate, registry, citation-provenance, and evidence-boundary checks. Protocol
`0.19.0` adds bounded retrieval quality fields and a `RetrievalQualityReport` contract for
local-source relevance, metadata-completeness, duplicate, accepted-source, and rejected-source
accounting. Retrieval quality remains literature context only and cannot establish novelty,
validation, correctness, exhaustive coverage, or publication readiness. Protocol `0.18.0` adds
backward-compatible citation-requirement metadata to claim-support audit
items. Citation requirements now distinguish positive external/source/literature claims from
current-run scaffold, missing-retrieval, and evidence-boundary statements. Protocol `0.17.0` added
semantic claim-adjudication fields and a `ClaimAdjudication` model. Adjudication classifies
sentence meaning only; deterministic artifact, citation-registry, bibliography, and evidence
checks remain authoritative. Adjudication reports remain non-evidence and cannot imply publication
readiness.

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
- `RetrievalQualityReport` generated from bounded source-quality and relevance filter reports;
- `SourceRelevanceAdjudication` generated from bounded source relevance judgments;
- `CitationRecord`, `CitationRegistry`, `BibliographyEntry`, `CitationUsage`, `ClaimAdjudication`,
  `CitationSafetyReport`, `ClaimSupportItem`, `ClaimSupportAuditReport`,
  `LiteratureGapStatement`, `LiteraturePositioningContract`, and `LiteraturePositioningReport`
  generated from citation-safe manuscript/literature models;
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
- `LLMBudgetConfig`, `LLMBudgetUsage`, `LLMBudgetDecision`, `LLMCallAccountingRecord`,
  `LLMRunSafetyReport`, `LLMOrchestrationConfig`, `LLMOrchestrationStep`,
  `LLMOrchestrationReport`, and `LLMOrchestrationResult` generated from explicit gated
  LLM-assisted paper orchestration models;
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
  --new-version 0.21.0
```

See [`versioning.md`](versioning.md) for MAJOR/MINOR/PATCH rules and
[`server-readiness.md`](server-readiness.md) for the future server contract boundary.
