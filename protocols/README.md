# fActorI Protocol Contracts

This directory contains language-neutral developer contracts generated from the repository's
existing Pydantic models. They are intended for Python tools, future Rust crates, future servers,
and coding agents that need a stable boundary without importing the Python runtime.

Protocol `0.86.0` adds the bounded read-only `replay.verify_core` kernel operation. It verifies the
current completed ledger snapshot, artifact inventory, manifest prefix, required outputs, and
claim/evidence dependencies without granting authority or writing provenance.

Protocol `0.85.0` adds the read-only `checkpoint.verify` kernel operation. It validates the latest
hash-linked autonomous-paper checkpoint chain and derives bounded resume permission without
returning evidence, label, scientific-validation, human-approval, or publication authority.

Protocol `0.84.0` adds the read-only `claim.resolve` kernel operation. It resolves one bounded
claim against a same-request, revalidated persisted evidence locator without returning authority.

Protocol `0.83.0` adds the read-only `evidence.validate_bundle` kernel operation. It validates
complete persisted Lean or local synthetic Stage C bundles without returning evidence authority,
a verification label, or a reusable capability.

Protocol `0.82.0` adds the read-only `evidence.classify` kernel operation. It classifies persisted
artifacts as context, presentation, or non-authoritative proof/experiment capability candidates;
it never grants verification authority or returns a verification label.

Protocol `0.82.0` adds the read-only `artifact.verify` kernel operation. It checks confined artifact
locations, type-directory mapping, raw-byte SHA-256 hashes, presentation restrictions, and exact
same-run producer-commit links. The operation is exposed through a development-compatibility
shadow bridge and creates no evidence or authority.

Protocol files are developer interfaces only. They are not run provenance, scientific evidence,
verification evidence, or a replacement for the append-only research ledger.

Protocol `0.78.0` adds explicitly gated final-paper PDF rendering from verified M106 LaTeX,
append-only compile reports, persisted standalone source and PDF hashes, optional targeted-study
tail integration, and inclusion of successful renders in hash-locked bundles. Rendered PDFs remain
presentation context only and create no evidence, validation, approval, or publication readiness.

Protocol `0.77.0` enlarges opt-in adaptive-study ceilings while preserving conservative defaults:
up to 64 questioner iterations, 32 code repairs, 8 plan repairs, and an 8-attempt no-progress
window. Explicit total-call and dollar budgets remain mandatory and authoritative; larger ceilings
do not grant evidence, validation, or publication authority.

Protocol `0.76.0` raises the bounded adaptive-questioner iteration ceiling from 20 to 24 so a
resumed repair at the former ceiling can still receive a post-repair reassessment. External-call
and cost budgets remain mandatory runtime limits, and the loop creates no evidence or publication
authority.

Protocol `0.75.0` adds explicit targeted-study OpenAI reasoning-effort and request-timeout settings.
The default effort preserves provider behavior, while low, medium, and high are persisted in run
configuration. Effort and timeout may only increase across resume. These transport settings remain
generation context and create no evidence, validation, or publication authority.

Protocol `0.74.0` adds a bounded adaptive evidence loop for full targeted studies. A non-fake LLM
questioner checks implementation fidelity, numerical validity, controls, evidence sufficiency,
claim scope, and stopping after M103; deterministic policy may route one append-only code or
evidence-plan repair, accept an honest supported or negative result, or stop on weak evidence,
no progress, or budget exhaustion. Metrics remain sandbox-output-only, the M104-M106 call tail is
reserved, and the loop creates no proof, novelty, real-world validation, or publication readiness.

Protocol `0.73.0` adds generic targeted research briefs and preflight, smoke, full-run, checkpoint,
resume, and inspection contracts for a human-selected one-branch study. Targeted orchestration
reuses the production M98-M106 stages without hardcoding a scientific domain and preserves all
existing evidence and publication-readiness boundaries.

Protocol `0.72.0` adds deterministic final-paper assembly, manifest, artifact/table/figure/
appendix/open-obligation records, local final-paper verification findings, and read-only inspection.
Assembly rebuilds tables only from completed sandbox output JSON through validated metric extraction
provenance, resolves real-retrieval citations, produces a hash-locked release directory, and never
creates evidence, proof verification, novelty proof, real-world validation, or publication readiness.

Protocol `0.71.0` adds nucleus-centered manuscript planning, artifact-bound claim and citation
bindings, bounded Markdown/LaTeX drafts, manuscript critic reviews, critic-guided revision reports,
raw-call provenance, and read-only inspection. Local validation copies metrics only from persisted
execution artifacts and rejects proof, novelty, real-world-validation, and publication-readiness
claims that exceed the available evidence.

Protocol `0.70.0` adds scientific-critic ensemble and cross-package adjudication contracts,
including role-scoped critic findings and reviews, local score aggregation, package decisions,
bounded paper-nucleus selection, raw-call provenance, and read-only inspection. Critics and
adjudicators cannot create metrics, proof verification, novelty proof, real-world validation, or
publication readiness.

Protocol `0.69.0` adds hybrid evidence-package planning and execution contracts, including typed
artifact plans for symbolic drafts, numerical illustrations, executable experiments, benchmarks,
counterexample searches, retrieval novelty-risk checks, negative controls, and robustness sweeps.
Hybrid packages preserve claim boundaries, require compatible evidence labels, source metrics only
from sandbox artifacts, keep symbolic/proof artifacts draft-labeled unless checked, and create no
real-world validation or publication readiness.

Protocol `0.68.0` adds non-fake LLM experiment-code contracts, deterministic static safety audits,
offline sandbox execution records, output-JSON-only metric extraction, bounded result labels, and
read-only inspection. Generated code and failed executions cannot invent metrics, and these
artifacts create no real-world validation or publication readiness.

Protocol `0.67.0` adds non-fake LLM route adjudication and execution-spec planning contracts,
including typed inputs and outputs, baselines, controls, negative controls, robustness plans,
metrics, success/failure criteria, proof obligations, retrieval queries, route-specific allowed
labels, forbidden claims, raw-call provenance, and inspection. These plans execute nothing and
create no evidence, real-world validation, or publication readiness.

Protocol `0.66.0` adds non-fake LLM ScientificSubstrate construction contracts, including concrete
model objects, notation, assumptions, baselines, bounded verification designs, result schemas,
negative controls, failure modes, route hints, scientific scores, raw-call provenance, and
read-only inspection. Deterministic selection is capacity/diversity infrastructure over LLM-authored
content; it creates no scientific validation or publication readiness.

Protocol `0.65.0` adds non-fake LLM variance-generation contracts, scientific variant scores and
batches, secret-free raw-call provenance, deterministic IdeaTree construction reports, and
optional source-pair, source-opportunity, variant-family, backend, and retrieval-context metadata
on IdeaTree nodes. Tree construction remains deterministic infrastructure over LLM-authored
content and creates no evidence or publication readiness.

Protocol `0.64.0` adds retrieval-contextualized deep opportunity discovery contracts: bounded
source summaries, per-pair retrieval contexts, concrete Q/H/T/E/B opportunities, non-fake LLM
scientific scores, secret-free raw-call provenance, append-only reports, and read-only inspection.
Novelty and underuse remain hypotheses, mocked retrieval remains non-production, and no discovery
artifact creates scientific validation or publication readiness.

Protocol `0.63.0` adds curated domain/method atlas entries, exclusion-only compatibility pairs and
reports, strict LLM pair-ranking prompt/result/report contracts, and diversity-constrained atlas
scan inspection. Atlas metadata and negative compatibility filtering are production infrastructure;
scientific pair ranking requires a recorded non-fake LLM backend, and novelty/underuse remain
hypotheses until retrieval evidence exists.

Protocol `0.62.0` adds backend-kind and scientific-stage classifications, explicit stage backend
records, and fail-closed production-mode policy, violation, and report contracts. Strict mode
rejects fake, fixture, deterministic-template, and heuristic scientific generation or judgment
while preserving deterministic infrastructure, local execution, metric computation, claim audit,
and bundle verification authority boundaries.

Protocol `0.61.0` adds route execution status, input/output contracts, immutable execution specs,
bounded deterministic results, aggregate reports, and read-only inspection contracts. M95 executes
only approved offline synthetic, benchmark, and applied-math templates; outputs remain scoped and
do not establish real-world validation or publication readiness.

Protocol `0.60.0` adds deterministic branch-route types, execution hints, per-substrate decisions,
append-only route plans, and read-only inspection contracts. Routing selects only the next bounded
workflow class; it executes nothing and creates no evidence, validation, or publication readiness.

Protocol `0.59.0` adds diversity-constrained substrate-promotion configuration, scored candidate,
decision, report, and inspection contracts plus explicit links from variance IdeaTree nodes to
their concrete ScientificSubstrates. Promotion remains planning context and creates no evidence,
validation, or publication readiness.

Protocol `0.58.0` adds opportunity-seeded variance configuration, candidate, batch, diversity,
report, and inspection contracts plus explicit IdeaTree opportunity-source links. These contracts
expand Stage 0 seeds into context-only pre-selection branches and do not create evidence,
validation, or publication readiness.

Protocol `0.57.0` adds Stage 0 opportunity discovery contracts for extracted domain primitives,
general method lenses, scored opportunity candidates, easy-win score breakdowns, promoted seed
constraints, append-only discovery reports, and read-only inspection payloads. These contracts are
creative-search context only and do not create evidence, validation, or publication readiness.

Protocol `0.56.0` adds generation-dependent mutation context, operator, candidate, semantic
diversity-check, plan, and inspection contracts. These contracts condition fresh scientific
branches on the current tournament winner and prior search history without creating evidence or
publication readiness.

Protocol `0.55.0` adds bounded recursive creative-search configuration, cycle, lineage, stop-reason,
controller-report, and inspection contracts. The controller composes existing deterministic local
idea, substrate, tournament, mutation, manuscript, bundle, and verification stages without creating
scientific validation or publication readiness.

Protocol `0.54.0` adds second-generation mutation tournament contracts. Mutation tournament specs,
entries, comparisons, results, and inspection reports compare the previous bounded winner against
experimentally routed mutation substrates while retaining synthetic-only evidence boundaries and
publication readiness false.

Protocol `0.53.0` adds tournament-driven creative mutation contracts. Mutation candidates, plans,
application reports, inspection reports, and mutation operators connect bounded tournament
feedback to new IdeaTree branches and ScientificSubstrate candidates without creating scientific
validation or publication readiness.

Protocol `0.52.0` adds deterministic multi-substrate tournament contracts. Tournament specs,
entries, comparisons, results, and inspection reports compare serious substrate branches using
declared synthetic-scope experiment metrics, select a bounded manuscript branch, and retain
alternative branches without creating real-world validation or publication readiness.

Protocol `0.51.0` adds substrate-specific experiment specifications, routing reports, bounded
results, and baseline-versus-method comparison tables. The selected distance-decay substrate can
now execute through the approved offline uv sandbox; successful results support only their mapped
synthetic claim and negative results remain visible without publication authority.

Protocol `0.50.0` adds context-only `ScientificSubstrate` contracts that instantiate concrete
scientific objects from idea-space mutation axes. Substrates include model equations, notation,
assumptions, DGP or dataset boundaries, baselines, measurable hypotheses, result schemas,
limitations, and failure modes without creating evidence, validation authority, or publication
readiness.

Protocol `0.49.0` adds context-only idea-space feature vectors, PCA-like axis diagnostics,
near-duplicate/collapsed-axis diagnostics, diversity reports, and inspection payloads over
`IdeaTree` nodes. These diagnostics expose creative variance and recommended future mutation axes
without creating scientific evidence, validation authority, or publication readiness.

Protocol `0.48.0` adds the first-class, context-only `IdeaTree`, node, edge, inspection, and export
contracts. The tree reconstructs Stage A candidates, Stage B variants, Stage C selection, pruning,
and the final manuscript branch from existing immutable run artifacts without creating evidence,
upgrading labels, or implying publication readiness.

Protocol `0.47.0` adds base-generation root-cause diagnostics to autonomous paper controller
reports. The diagnostics expose candidate and Stage A/B/C counts, manuscript-plan presence, and
budget-blocked components without weakening safety gates, creating evidence, or changing
publication readiness.

Protocol `0.46.0` adds immutable autonomous paper checkpoints, checkpoint-index snapshots, and
append-only resume reports. Resume verifies checkpoint content, stage artifacts, protocol version,
claim/citation safety, bundle hashes, and ledger ancestry before reuse; final bundle verification
always reruns and the reliability artifacts create no evidence or publication readiness.

Protocol `0.45.0` adds one-command autonomous paper run stages, handoff decisions, aggregate
controller reports, and latest-run indexes. The controller composes existing safety-gated stages,
keeps publication readiness false, and fails closed when generation, regeneration, bundle
assembly, or independent bundle verification is unsafe or incomplete.

Protocol `0.44.0` adds independent read-only final bundle verification checks, replay-by-inspection
summaries, and aggregate verification reports. Verification consumes only the selected bundle
after optional run-id lookup, never rewrites the bundle, reruns commands, creates evidence, or
implies publication readiness.

Protocol `0.43.0` adds deterministic final release bundle, artifact manifest, reproducibility
manifest, hash-lock, bundle report, and bundle index contracts. Release bundles package the final
manuscript, accepted citations, scoped evidence artifacts, reports, and reproducibility metadata
only; they do not create evidence, validation, or publication readiness.

Protocol `0.41.0` adds fail-closed local/offline capability escalation reports and policies for
deferred proof and retrieval paths. Local proof-plan refinement remains non-verification context,
fixture-backed formal support requires exact passed-checker scope, and local source expansion
continues through retrieval-quality and registry acceptance checks.

Protocol `0.40.0` adds attempt-, budget-, and strategy-aware autonomous-loop terminal
classification for mixed resolved/deferred states. It records per-gap terminal dispositions,
effective automation readiness after history, explicit terminal reasons, and early completion
without treating deferred work as evidence or publication readiness.

Protocol `0.42.0` adds deterministic final evidence-aware manuscript sections, scoped claim
dispositions, structured manuscript documents, append-only regeneration reports, and latest
regeneration indexes. Regeneration consumes existing evidence and audit state only; it does not
create evidence, validation, or publication readiness.

Protocol `0.39.0` adds bounded empirical demonstration gap creation, autonomous
experiment-demand counts, and loop/routing/sandbox summaries for uv-local empirical coverage.
These fields remain workflow context only and do not imply validation or publication readiness.

Protocol `0.38.0` adds approved local experiment template registries, deterministic
experiment-gap routing reports, sandbox-compatible routed experiment specs, and loop-level sandbox
budget reports. Template routing is workflow only; completed sandbox artifacts still require
existing experiment-artifact intake and remain scoped to mapped bounded result claims.

Protocol `0.37.0` adds gated uv-local Python experiment sandbox manifests, append-only run reports,
and derived sandbox indices. The sandbox uses approved local bundles, offline dependency policy,
fixed seeds, timeouts, resource limits, logs, metrics, and content hashes. Only successful outputs
that pass existing experiment-artifact intake can support a mapped bounded result claim; sandbox
reports remain non-evidence and cannot imply publication readiness.

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
