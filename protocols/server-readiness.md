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
  `GeneratedSectionDraft`, and `HumanReviewDecision`.
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
- Docker experiments, empirical data ingestion, full manuscript drafting, polished prose generation,
  final LaTeX generation,
  and external review
  readiness are not implemented.

## Mutating vs Read-Only Operations

Mutating stages from Stage A through export preparation must append ledger commits and write
content-hashed artifacts. A future server should map these to explicit jobs and preserve the
current rerun policy:

- `FailIfExists` is the safe default.
- `SkipIfComplete` is an explicit no-op resume.
- `AllowIfForced` requires a deliberate force request.
- read-only operations remain rerunnable and must not create ledger commits.

Read-only operations include status, resume validation, dry-run planning, replay verification,
diagnostics, cross-run comparison, hygiene inspection, remediation planning, protocol export,
protocol validation, compatibility checks, and version checks.

## Adapter and Evidence Rules

External-call flags must be explicit in any future API. Adapter outputs that affect a run must be
validated, written through the artifact store, and ledgered by the owning stage. LLM output,
reviewer output, retrieval output, Markdown, LaTeX, protocol schemas, export plans, diagnostics,
and replay reports are not verification evidence. A future server may expose the Lean proof
backend only as an explicit external-tool job with the same proof contract, executable gate, and
proof-evidence safety checks used by the CLI. It may expose the local synthetic experiment backend
only as an explicit external-tool job with the same experiment contract, runner gate,
SyntheticOnly data boundary, metric acceptance checks, and synthetic-evidence safety checks.
A future server may expose one-section prose drafting only as an explicit external-call job with the
same prose contract, allowed claim/evidence/citation lists, and safety report checks used by the CLI.
Generated prose remains manuscript context and cannot mutate claims, evidence, citations, or labels.
