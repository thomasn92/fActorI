# Server Readiness Notes

This repository does not include FastAPI, a server entry point, Rust code, or production job
orchestration. The protocol layer is now broad enough for a future server or Rust tool to consume
run-control and adapter contracts without importing Python.

## Server-Ready Contracts

- Run control: `PipelineRunConfig`, `PipelineRunReport`, `RunStatusReport`,
  `ResumeValidationReport`, `StageCheckpoint`, `StageRerunDecision`, and
  `LedgerTipValidationReport`.
- Adapter I/O: `AdapterConfig`, `LLMPromptContract`, `LLMCandidateParseReport`,
  `LLMReviewerPromptContract`, `LLMReviewerParseReport`, `RetrievalQuery`, `RetrievalResult`,
  `RetrievedDocument`, `RetrievalRunReport`, `RetrievalParseReport`,
  `RetrievalAdequacyCertificate`, `ProofVerificationContract`, `ProofVerificationResult`,
  `ExperimentRunContract`, `ExperimentRunResult`, `ProseSectionContract`, `ProsePromptContract`,
  `ProseGenerationRequest`, `ProseGenerationParseResult`, `ProseSafetyReport`,
  `GeneratedSectionDraft`, `ManuscriptDraftingPlan`, `SectionDraftingTask`,
  `SectionDraftingResult`, `CompleteMarkdownDraft`, `ManuscriptDraftingReport`,
  `ManuscriptAssemblyReport`, `CitationRecord`, `CitationRegistry`, `BibliographyEntry`,
  `CitationUsage`, `CitationSafetyReport`, `LiteratureGapStatement`,
  `LiteraturePositioningContract`, `LiteraturePositioningReport`, `LatexExportContract`,
  `LatexSourceMap`, `LatexSafetyReport`, `LatexRenderConfig`, `LatexRenderResult`,
  `LatexCompileCheckReport`, `LatexExportResult`, `PaperCriticFinding`, `PaperCriticReport`,
  `PaperReleaseReadinessPreview`, `SectionRevisionPlan`, `PaperRevisionPlan`,
  `PaperRevisionPatch`, `RevisionSafetyReport`, `PaperRevisionResult`,
  `FullPaperGenerationConfig`, `FullPaperGenerationStep`, `FullPaperArtifactBundle`,
  `FullPaperGenerationReport`, `FullPaperGenerationResult`, and `HumanReviewDecision`.
- Manifests and outputs: `ArtifactManifest`, `ResearchObjectManifest`,
  `ReproducibilityManifest`, `RunSummary`, `ResearchObject`, `PaperSkeleton`,
  `FinalAuditReport`, `ReleaseGateDecision`, `ExportReadinessReport`,
  `ReplayVerificationReport`, `DiagnosticReport`, `OutputHygieneReport`, and
  `HygieneRemediationPlan`.
- Important enums are exported as top-level schemas or stable `$defs`.

## Not Server-Ready Yet

- No server process, request router, authentication, queue, or concurrency model exists.
- No production idempotency lock or database transaction layer exists beyond local ledger checks.
- Real adapters remain explicitly gated; fake adapters are still the default.
- Real Lean proof checking and local synthetic experiment execution have gated local-tool adapter
  seams and are disabled by default.
- Docker experiments, empirical data ingestion, polished prose generation, hard PDF-generation
  dependencies, publication-ready LaTeX, and external review readiness are not implemented.

## Mutating vs Read-Only Operations

Mutating stages from Stage A through export preparation must append ledger commits and write
content-hashed artifacts. A future server should map these to explicit jobs and preserve the
current rerun policy:

- `FailIfExists` is the safe default.
- `SkipIfComplete` is an explicit no-op resume.
- `AllowIfForced` requires a deliberate force request.
- read-only operations remain rerunnable and must not create ledger commits.

Read-only operations include status, resume validation, dry-run planning, replay verification,
diagnostics, cross-run comparison, hygiene inspection, remediation planning, paper critique,
protocol export, protocol validation, compatibility checks, and version checks.

## Adapter and Evidence Rules

External-call flags must be explicit in any future API. Adapter outputs that affect a run must be
validated, written through the artifact store, and ledgered by the owning stage. LLM output,
reviewer output, retrieval output, Markdown, LaTeX, protocol schemas, export plans, diagnostics,
and replay reports are not verification evidence. A future server may expose the Lean proof
backend only as an explicit external-tool job with the same proof contract, executable gate, and
proof-evidence safety checks used by the CLI. It may expose the local synthetic experiment backend
only as an explicit external-tool job with the same experiment contract, runner gate,
SyntheticOnly data boundary, metric acceptance checks, and synthetic-evidence safety checks.
A future server may expose section-level or complete Markdown prose drafting only as explicit
external-call jobs with the same prose contracts, allowed claim/evidence/citation lists, and safety
report checks used by the CLI. Generated prose and Markdown drafts remain manuscript context and
cannot mutate claims, evidence, citations, or labels.
Citation-registry and literature-positioning operations may be exposed as manuscript-context jobs
only. They must preserve deterministic citation keys, reject unknown/invented citations, and avoid
claiming exhaustive coverage, novelty proof, proof evidence, experiment evidence, human approval, or
scientific validation.
LaTeX export may be exposed as a presentation/export job. Render checks must be modeled as gated
external-tool jobs with explicit executable configuration. LaTeX source, bibliography placeholders,
source maps, render reports, and rendered PDFs remain presentation artifacts only and cannot imply
publication readiness or create proof, experiment, retrieval, human-review, or scientific-validation
evidence.
Paper critique may be exposed as a read-only manuscript-quality job. Paper revision must be exposed
as an explicit manuscript/revision job that preserves claim/evidence tables, citation registries,
and labels. Revision artifacts remain context/presentation artifacts only and cannot imply peer
review, publication readiness, proof evidence, experiment evidence, retrieval evidence, human
approval, or scientific validation.
Full-paper generation may be exposed as a mutating manuscript-package orchestration job over
existing citation, drafting, LaTeX export, critique, and optional safe fake revision steps. A server
must keep revision and render checks explicitly gated, preserve rerun policy, and report generated
paper packages as context/export artifacts only. Full-paper generation cannot invent upstream
content, create or upgrade evidence labels, mutate claim/evidence tables, invent citations, or imply
publication readiness.
