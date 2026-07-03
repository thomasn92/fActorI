# Canonical Commands

Run commands from the repository root.

## Environment

```bash
uv sync --dev
```

## Protocol Contracts

Export the checked-in language-neutral JSON Schemas and deterministic examples:

```bash
uv run factori export-protocols
```

Verify that generated files are current without rewriting them:

```bash
uv run factori export-protocols --check
```

Validate deterministic example payloads against exported JSON Schemas:

```bash
uv run factori validate-protocol-examples
uv run factori validate-protocol-examples --json
```

An isolated consumer export can use `--output-dir <path>/jsonschema`. Protocol export is outside
run provenance: it creates no ledger commits, touches no run artifact manifests, and produces no
verification evidence.

Compare two exported schema versions without writing either directory:

```bash
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema
uv run factori check-protocol-compat \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --json --fail-on-breaking
```

The checker is conservative. Unknown composition or reference changes require manual review. The
optional-property removal policy is breaking because generated consumers may depend on the field.

Check whether the protocol version bump is sufficient for a schema change:

```bash
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --old-version 0.1.0 \
  --new-version 0.13.0
uv run factori check-protocol-version \
  --old-dir path/to/old/jsonschema \
  --new-dir path/to/new/jsonschema \
  --json
```

Protocol validation, compatibility, and version checks are read-only developer-contract commands.
They do not create run artifacts, ledger commits, or verification evidence.

Schema package maintenance has no runtime command. Public callers should continue to import from
`factori.schemas`; validate schema refactors with `export-protocols --check`,
`validate-protocol-examples`, pytest, and Ruff.

Selected CLI commands now delegate to typed library entry points under `factori.commands`. The user
commands and output remain the compatibility surface.

## Deterministic Pipeline

Canonical one-command run:

```bash
uv run factori run-all --run-id demo --domain "human geography"
```

Common controls:

```bash
uv run factori run-all --run-id demo-a --domain "human geography" --method "optimal transport"
uv run factori run-all --run-id demo-b --domain "human geography" --stop-after run-stage-c
uv run factori run-all --run-id demo-b --domain "human geography" --start-at synthesize-abstract
uv run factori run-all --run-id demo-c --domain "human geography" --skip-replay
uv run factori run-all --run-id demo-d --domain "human geography" --run-diagnostics
uv run factori run-all --run-id demo-e --domain "human geography" --run-diagnostics \
  --write-replay-report --write-diagnostic-report
uv run factori run-all --run-id demo-f --domain "human geography" --fail-fast
uv run factori run-all --run-id demo-g --domain "human geography" --dry-run
uv run factori plan-run --run-id demo-h --domain "human geography" --json
```

Mutating commands fail if their completion artifacts already exist. Resume safely by selecting a
later stage, skip completed stages explicitly, or force only under the force-aware policy:

```bash
uv run factori run-all --run-id demo --domain "human geography" \
  --rerun-policy skip-if-complete
uv run factori run-stage-a --run-id demo --domain "human geography" \
  --rerun-policy allow-if-forced --force
uv run factori validate-ledger-tip --run-id demo
```

`fail-if-exists` is the default for all mutating stage commands and `run-all`. Read-only commands
remain rerunnable. `validate-ledger-tip` reports hash-chain failures, broken parent links, forks, multiple tips, and
repeated mutating-stage start markers without changing the ledger or artifact manifest.

`run-all` calls existing stage functions directly. Its pipeline report is hashed and ledgered;
replay and diagnostics remain read-only, and their optional reports remain outside provenance.
When `--start-at` is used, `run-all` first validates the requested resume point against explicit
checkpoint artifacts and blocks before mutation if prerequisites are missing.
Dry-run planning is read-only and reports selected stages, blockers, and expected outputs without
creating artifacts or ledger commits.

Equivalent individual commands:

```bash
uv run factori run-stage-a --run-id demo --domain "human geography"
uv run factori run-stage-b --run-id demo
uv run factori select-stage-c --run-id demo
uv run factori run-stage-c --run-id demo
uv run factori run-stage-c --run-id demo \
  --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-stage-c --run-id demo \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
uv run factori synthesize-abstract --run-id demo
uv run factori plan-manuscript --run-id demo
uv run factori critique-paper-shape --run-id demo
uv run factori critique-paper-shape --run-id demo --write-report
uv run factori critique-paper-shape --run-id demo --json
uv run factori generate-section-draft --run-id demo --section-id introduction
uv run factori generate-section-draft --run-id demo --section-id introduction --write-report
uv run factori generate-section-draft --run-id demo --section-id introduction --json
uv run factori draft-manuscript --run-id demo
uv run factori draft-manuscript --run-id demo --write-report
uv run factori draft-manuscript --run-id demo --json
uv run factori build-citation-registry --run-id demo
uv run factori build-citation-registry --run-id demo --write-report
uv run factori build-citation-registry --run-id demo --json
uv run factori draft-manuscript --run-id demo --include-citations
uv run factori export-latex --run-id demo
uv run factori export-latex --run-id demo --write-report
uv run factori export-latex --run-id demo --json
uv run factori export-latex --run-id demo \
  --render-check --allow-external-tools --latex-executable pdflatex
uv run factori critique-paper --run-id demo
uv run factori critique-paper --run-id demo --write-report
uv run factori critique-paper --run-id demo --json
uv run factori revise-paper --run-id demo
uv run factori revise-paper --run-id demo --json
uv run factori revise-paper --run-id demo \
  --apply-safe-fake-revision --write-report
uv run factori build-autonomous-evidence-plan \
  --run-id demo --planner-backend deterministic
uv run factori inspect-autonomous-evidence-plan --run-id demo
uv run factori inspect-autonomous-evidence-plan --run-id demo --json
uv run factori execute-autonomous-evidence-plan \
  --run-id demo --execution-mode dry-run --executor-backend deterministic
uv run factori execute-autonomous-evidence-plan \
  --run-id demo --execution-mode apply --executor-backend deterministic
uv run factori inspect-autonomous-plan-execution --run-id demo
uv run factori inspect-autonomous-plan-execution --run-id demo --json
uv run factori execute-planned-specs \
  --run-id demo --execution-mode dry-run --spec-executor-backend deterministic_local
uv run factori execute-planned-specs \
  --run-id demo --execution-mode apply --spec-executor-backend deterministic_local
uv run factori inspect-planned-spec-execution --run-id demo
uv run factori inspect-planned-spec-execution --run-id demo --json
uv run factori run-python-experiment-sandbox \
  --run-id demo --experiment-spec path/to/experiment-spec.json \
  --sandbox-backend uv_local --execution-mode dry-run
uv run factori run-python-experiment-sandbox \
  --run-id demo --experiment-spec path/to/experiment-spec.json \
  --sandbox-backend uv_local --execution-mode apply
uv run factori inspect-python-experiment-sandbox --run-id demo
uv run factori inspect-python-experiment-sandbox --run-id demo --json
uv run factori route-experiment-gaps \
  --run-id demo --routing-backend deterministic
uv run factori inspect-experiment-gap-routing --run-id demo
uv run factori inspect-experiment-gap-routing --run-id demo --json
uv run factori run-autonomous-loop \
  --run-id demo --loop-backend deterministic --max-iterations 3 \
  --max-attempts-per-gap 2
uv run factori run-autonomous-loop \
  --run-id demo --loop-backend deterministic --max-iterations 6 \
  --max-attempts-per-gap 1 --enable-strategy-diversification
uv run factori run-autonomous-loop \
  --run-id demo --loop-backend deterministic --max-iterations 4 \
  --max-attempts-per-gap 1 --enable-experiment-routing \
  --enable-empirical-demonstration-gaps \
  --python-sandbox-backend uv_local --max-sandbox-runs-per-loop 3 \
  --max-sandbox-runs-per-iteration 1
uv run factori inspect-autonomous-loop --run-id demo
uv run factori inspect-autonomous-loop --run-id demo --json
uv run factori regenerate-final-manuscript \
  --run-id demo --regeneration-backend deterministic
uv run factori inspect-final-manuscript --run-id demo
uv run factori inspect-final-manuscript --run-id demo --json
uv run factori inspect-idea-tree --run-id demo
uv run factori inspect-idea-tree --run-id demo --json
uv run factori export-idea-tree --run-id demo --format markdown
uv run factori export-idea-tree --run-id demo --format json
uv run factori inspect-idea-space --run-id demo
uv run factori inspect-idea-space --run-id demo --json
uv run factori export-idea-space-report --run-id demo --format markdown
uv run factori export-idea-space-report --run-id demo --format json
uv run factori build-final-release-bundle --run-id demo
uv run factori inspect-final-release-bundle --run-id demo
uv run factori inspect-final-release-bundle --run-id demo --json
uv run factori run-substrate-tournament --run-id demo
uv run factori inspect-substrate-tournament --run-id demo
uv run factori inspect-substrate-tournament --run-id demo --json
uv run factori verify-final-release-bundle \
  --bundle-path runs/demo/release-bundles/final-bundle-0001
uv run factori verify-final-release-bundle --run-id demo --json
uv run factori verify-final-release-bundle --run-id demo --write-report
uv run factori diversify-gap-strategies \
  --run-id demo --strategy-backend deterministic
uv run factori inspect-gap-strategy-diversification --run-id demo
uv run factori inspect-gap-strategy-diversification --run-id demo --json
uv run factori inspect-gap-attempt-history --run-id demo
uv run factori inspect-gap-attempt-history --run-id demo --json
uv run factori inspect-planned-spec-dedup --run-id demo
uv run factori inspect-planned-spec-dedup --run-id demo --json
uv run factori ingest-reviewer-change-requests \
  --run-id demo --request-file path/to/reviewer-change-requests.json
uv run factori inspect-reviewer-change-requests --run-id demo
uv run factori inspect-reviewer-change-requests --run-id demo --json
uv run factori reconcile-human-review --run-id demo
uv run factori inspect-human-review-reconciliation --run-id demo
uv run factori inspect-human-review-reconciliation --run-id demo --json
uv run factori generate-paper --run-id demo
uv run factori generate-paper --run-id demo --write-report
uv run factori generate-paper --run-id demo --json
uv run factori generate-paper --run-id demo \
  --apply-safe-fake-revision --write-report
uv run factori run-llm-paper \
  --run-id llm-fake --domain "human geography" \
  --candidate-backend fake --reviewer-backend fake --prose-backend fake \
  --apply-safe-fake-revision --write-report
uv run factori build-draft-skeleton --run-id demo
uv run factori package-research-object --run-id demo
uv run factori assemble-paper-skeleton --run-id demo
uv run factori final-audit --run-id demo
uv run factori prepare-export --run-id demo
uv run factori replay-verify --run-id demo
uv run factori replay-verify --run-id demo --write-report
uv run factori diagnose-run --run-id demo
uv run factori diagnose-run --run-id demo --write-report
uv run factori compare-runs --baseline-run-id baseline --candidate-run-id candidate
uv run factori compare-runs --baseline-run-id baseline --candidate-run-id candidate --write-report
uv run factori status --run-id demo
uv run factori status --run-id demo --stage run-stage-b
uv run factori status --run-id demo --json
uv run factori validate-resume --run-id demo --start-at plan-manuscript
uv run factori run-all --run-id demo --domain "human geography" --dry-run
uv run factori plan-run --run-id demo --domain "human geography"
uv run factori plan-run --run-id demo --domain "human geography" --json
uv run factori inspect-hygiene --run-id demo
uv run factori inspect-hygiene --run-id demo --write-report
uv run factori inspect-hygiene --run-id demo --json
uv run factori plan-hygiene-remediation --run-id demo
uv run factori plan-hygiene-remediation --run-id demo --write-report
uv run factori plan-hygiene-remediation --run-id demo --json
uv run factori show-adapters
uv run factori adapters
```

Autonomous-loop inspection reports the terminal state and reason, resolved/deferred/exhausted/
duplicate-only gap counts, effective automation readiness after attempt history, and whether the
loop stopped before its configured iteration cap. Deferred proof, retrieval, and sandbox-budget
work remains visible and is not evidence or publication readiness.

The default adapter path is fake and makes no external calls. Gated OpenAI adapters are available
for Stage A candidate proposal and Stage B structural review. Each requires explicit backend/use,
external-call permission, and an API key. Models are configurable because availability can vary by
account:

```bash
OPENAI_API_KEY="<key>" uv run factori show-adapters \
  --backend openai --allow-external-calls --llm-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-stage-a \
  --run-id llm-demo --domain "human geography" --method "optimal transport" \
  --adapter-backend openai --allow-external-calls --llm-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-all \
  --run-id llm-pipeline --domain "human geography" --method "optimal transport" \
  --adapter-backend openai --allow-external-calls --llm-model gpt-5-mini
```

Stage B LLM reviewers provide critique and scores only. They have no verification, publication,
proof, experiment, retrieval, or human-approval authority:

```bash
OPENAI_API_KEY="<key>" uv run factori show-adapters \
  --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori run-all \
  --run-id reviewer-pipeline --domain "human geography" \
  --reviewer-backend openai --use-llm-reviewers \
  --allow-external-calls --reviewer-model gpt-5-mini
```

Prose drafting is separately gated. The fake backend is default and produces placeholder section
text from approved manuscript contracts. `generate-section-draft` drafts one section. `draft-manuscript`
uses the same prose adapter section by section and assembles a complete Markdown draft. Neither
command can create claims, invent citations or bibliography entries, create evidence, upgrade
labels, generate polished prose, produce LaTeX evidence, or claim publication readiness:

```bash
uv run factori generate-section-draft \
  --run-id demo --section-id introduction
uv run factori generate-section-draft \
  --run-id demo --section-id introduction --write-report
uv run factori draft-manuscript --run-id demo --write-report
uv run factori build-citation-registry --run-id demo --write-report
uv run factori draft-manuscript --run-id demo --include-citations --write-report
OPENAI_API_KEY="<key>" uv run factori generate-section-draft \
  --run-id demo --section-id introduction \
  --prose-backend openai --allow-external-calls --prose-model gpt-5-mini
OPENAI_API_KEY="<key>" uv run factori draft-manuscript \
  --run-id demo --prose-backend openai --allow-external-calls --prose-model gpt-5-mini
```

LaTeX export is presentation/export only. It converts a complete Markdown manuscript draft into
`paper.tex`, bibliography placeholders, a source map, and safety/export reports when
`--write-report` is used. Render checks are optional and fail closed unless external tools are
explicitly enabled and a LaTeX executable is configured:

```bash
uv run factori export-latex --run-id demo --write-report
uv run factori export-latex --run-id demo --json
uv run factori export-latex --run-id demo \
  --render-check --allow-external-tools --latex-executable pdflatex
```

Paper critique and revision are manuscript-quality operations. `critique-paper` is read-only by
default. `revise-paper` plans revisions by default; it writes a revised draft only with
`--apply-safe-fake-revision --write-report`. Revision artifacts are presentation/context only and
cannot invent citations, mutate claim/evidence tables, create verification evidence, upgrade
labels, or imply publication readiness:

```bash
uv run factori critique-paper --run-id demo --write-report
uv run factori revise-paper --run-id demo --json
uv run factori revise-paper --run-id demo \
  --apply-safe-fake-revision --write-report
```

Deterministic bounded citation-registry smoke, with no network or external API:

```bash
uv run factori run-llm-paper \
  --run-id local-citation-registry-smoke-001 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend fake \
  --reviewer-backend fake \
  --prose-backend fake \
  --enable-retrieval \
  --retrieval-backend fake \
  --max-retrieval-sources 5 \
  --citation-policy registry-only \
  --generate-paper \
  --enable-safe-repair \
  --write-report \
  --json
```

Retrieval remains disabled by default. With no populated registry, citation policy is `none` and
missing citations remain a quality warning. With retrieval enabled, citation policy is
`registry-only`: generated prose may use only persisted registry keys, bibliography entries come
only from registry metadata, and fixture records are marked `source_status=fixture`. Citations are
literature context only, not proof, experiment evidence, novelty validation, literature
completeness, or publication readiness.
Generated paper packages also write `reports/claim-support-audit.json`, which classifies sentences
and checks that source-context citations are local to the sentence or paragraph they support.
Scaffold/provenance/limitation statements do not require citations. Citations cannot support proof,
experiment, novelty, validation, or publication-readiness claims.

Deterministic local-source retrieval uses an explicit JSON file, no network, and quality filters
before citation registry construction:

```bash
uv run factori run-llm-paper \
  --run-id local-retrieval-quality-smoke-001 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend fake \
  --reviewer-backend fake \
  --prose-backend fake \
  --enable-retrieval \
  --retrieval-backend local \
  --retrieval-local-path tests/fixtures/retrieval/human_geography_sources.json \
  --max-retrieval-sources 8 \
  --citation-policy registry-only \
  --generate-paper \
  --enable-safe-repair \
  --write-report \
  --json
```

This writes `retrieval-quality-report.json` with retrieved, accepted, rejected, duplicate,
low-relevance, and metadata-incomplete counts. Rejected local sources do not enter
`citation-registry.json`; accepted sources remain bounded background context and cannot establish
proof, empirical validation, novelty, exhaustive coverage, or publication readiness.

Use deterministic semantic adjudication for local pipeline validation:

```bash
uv run factori run-llm-paper \
  --run-id local-llm-adjudicator-smoke-001 \
  --domain "human geography" \
  --candidate-backend fake --reviewer-backend fake --prose-backend fake \
  --claim-adjudicator-backend fake \
  --enable-retrieval --retrieval-backend fake \
  --citation-policy registry-only --generate-paper --write-report --json
```

The OpenAI adjudicator additionally requires `--allow-external-calls`, a configured
`OPENAI_API_KEY`, `--claim-adjudicator-model`, and an explicit
`--max-claim-adjudication-calls`. It batches only ambiguous sentences and shares total-call, token,
cost, and rate controls with candidate, reviewer, and prose calls. The LLM judges meaning;
deterministic code verifies registry keys, source scope, bibliography provenance, and evidence
artifacts.
After adjudication, missing-citation failures are limited to positive external/source/literature
claims. Current-run status, absence of retrieval support, scaffold role, retrieval limitations, and
evidence-boundary statements do not require citations.

Full-paper generation is an orchestration over the existing non-evidence manuscript workflow. By
default it builds or reuses citation registry/literature positioning artifacts, drafts the Markdown
manuscript, exports LaTeX, and runs the paper critic. Revision requires
`--apply-safe-fake-revision`; render diagnostics require `--render-check --allow-external-tools`
and an explicit LaTeX executable:

```bash
uv run factori generate-paper --run-id demo --write-report
uv run factori generate-paper --run-id demo --json
uv run factori generate-paper --run-id demo \
  --apply-safe-fake-revision --reexport-latex-after-revision --write-report
uv run factori generate-paper --run-id demo \
  --render-check --allow-external-tools --latex-executable pdflatex
```

Generated paper packages are manuscript/presentation/export context only. They cannot create or
upgrade evidence labels, mutate claim/evidence tables, invent citations, or imply publication
readiness.
The default drafting profile is quality-aware: it derives a non-placeholder title when possible,
uses a compact 7-section paper-shaped outline, gives prose contracts target word ranges and
no-evidence boundary instructions, omits empirical-results sections without experiment evidence,
and omits bibliography output when no retrieval-backed citations exist.
Semantic content is preferred over padding: prose contracts ask for problem framing, one bounded
central contribution, a mechanical method summary, evidence limitations, and provenance context.
Generated section bodies should not add Markdown headings; the assembler owns headings and demotes
nested generated headings so the planned paper shape remains coherent.
The prose safety layer distinguishes manuscript scaffolding from scientific claims. Safe
non-evidential statement classes include problem framing, method and pipeline description,
evidence-boundary statements, limitation statements, demonstration-status statements,
citation-status statements, provenance statements, and non-evidence disclaimers. Unsafe sentences
are removed at sentence level when safe content remains; required sections with no retained safe
content receive deterministic fallback text. This does not permit fake citations, proof labels,
conjecture/theorem language, empirical validation claims, or publication-readiness claims.

Evaluate a generated bundle for internal human-review handoff readiness. This is not publication
readiness, peer review, scientific validation, or evidence:

```bash
uv run factori evaluate-paper-release --run-id demo
uv run factori evaluate-paper-release --run-id demo --json
uv run factori evaluate-paper-release --run-id demo --write-report
uv run factori evaluate-paper-release --run-id demo \
  --max-major-findings 0 --require-latex-export --require-citations
```

Deterministic golden paper-generation smoke sequence:

```bash
uv run factori run-all --run-id golden-paper --domain "human geography"
uv run factori generate-paper --run-id golden-paper \
  --apply-safe-fake-revision --reexport-latex-after-revision --write-report
uv run factori evaluate-paper-release --run-id golden-paper --write-report
uv run factori export-protocols --check
uv run factori validate-protocol-examples
```

This sequence uses fake defaults, performs no render check, and asserts structural regression
behavior only. It does not imply scientific validation or publication readiness.

End-to-end LLM-assisted paper orchestration is separate from default `run-all`. Fake mode is a
local smoke path. Real mode requires explicit real backends, `--allow-external-calls`, API
credentials, and an explicit budget before any network call can be attempted:

```bash
uv run factori run-llm-paper \
  --run-id llm-fake --domain "human geography" \
  --candidate-backend fake --reviewer-backend fake --prose-backend fake \
  --apply-safe-fake-revision --write-report

OPENAI_API_KEY="<key>" uv run factori run-llm-paper \
  --run-id llm-real --domain "human geography" \
  --allow-external-calls \
  --candidate-backend openai --reviewer-backend openai --prose-backend openai \
  --candidate-model gpt-5-mini --reviewer-model gpt-5-mini --prose-model gpt-5-mini \
  --max-total-calls 50 --max-estimated-cost-usd 5.00 --rate-limit-per-minute 10 \
  --apply-safe-fake-revision --write-report
```

Live-smoke ladder for OpenAI diagnostics:

```bash
export OPENAI_API_KEY="..."

uv run factori run-llm-paper \
  --run-id live-smoke-preflight \
  --domain "human geography" \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend openai --reviewer-model gpt-5.4-mini \
  --prose-backend openai --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 29 --max-estimated-cost-usd 1.00 \
  --preflight-only --json

uv run factori run-llm-paper \
  --run-id live-candidate-smoke-004 \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend fake --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 3 --max-candidate-generation-calls 3 \
  --max-estimated-cost-usd 0.20 \
  --write-report --json

uv run factori run-llm-paper \
  --run-id live-candidate-budget-block \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend fake --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 1 --max-candidate-generation-calls 1 \
  --max-estimated-cost-usd 0.20 \
  --write-report --json

uv run factori run-llm-paper \
  --run-id live-smoke-002 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend openai --reviewer-model gpt-5.4-mini \
  --prose-backend openai --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 50 --max-estimated-cost-usd 1.00 \
  --generate-paper --evaluate-release --write-report --json
```

`--preflight-only` validates gates, credentials, selected backends/models, and explicit budget
without network calls or run mutation. OpenAI transport diagnostics include sanitized truncated
HTTP error bodies, redacted URLs, selected model, and request hashes; they do not include secrets.
OpenAI structured-output transport schemas are adapter-local strict copies. Every object property is
listed in `required`, optional values are nullable, and public fActorI protocol schemas are not
rewritten for OpenAI-specific compatibility.
Use `--llm-scope candidate-only` for isolated Stage A smoke tests. That scope never runs Stage B,
Stage C, `generate-paper`, or `evaluate-paper-release`, and it forces LaTeX export, critique, and
revision options off. Runtime budget guards authorize each real LLM transport call before the
external request; budget-blocked attempts are accounting records with `status=Blocked` and
`external_call_performed=false`.

Use `--llm-scope reviewer-only` to run Stage A and the Stage B reviewer path without Stage C,
paper generation, release evaluation, LaTeX export, critique, or revision. Stage B expands at most
four Stage A survivors into four children each, so a complete reviewer smoke plans 16 review calls.
With the three human-geography candidate calls, the explicit total is 19:

```bash
uv run factori run-llm-paper \
  --run-id live-reviewer-smoke-002 \
  --domain "human geography" \
  --llm-scope reviewer-only \
  --candidate-backend openai --candidate-model gpt-5.4-mini \
  --reviewer-backend openai --reviewer-model gpt-5.4-mini \
  --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 19 --max-candidate-generation-calls 3 \
  --max-review-calls 16 --max-estimated-cost-usd 1.00 \
  --write-report --json
```

If a runtime LLM budget blocks a mutating pipeline stage, full-paper orchestration records the
pipeline as blocked and skips paper generation and release evaluation.

Full-paper preflight counts all deterministic manuscript drafting tasks. The current manuscript
plan has 10 section tasks. If runtime prose generation still reaches a stricter token/cost/call
limit, `run-llm-paper --json` returns `LLMOrchestrationBlocked`; the blocked call is recorded with
`error_type=BudgetExceeded` and `external_call_performed=false`, without a traceback.

```bash
uv run factori run-llm-paper \
  --run-id live-prose-smoke-002 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend fake \
  --reviewer-backend fake \
  --prose-backend openai \
  --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 12 \
  --max-prose-calls 12 \
  --max-estimated-cost-usd 1.50 \
  --generate-paper \
  --write-report \
  --json
```

Add `--enable-safe-repair` for one deterministic post-drafting text repair before release
evaluation. It replaces explicit unsupported labels and publication-ready language, downgrades
synthetic-as-real claims, removes text marked unsafe/unsupported, adds a non-evidence limitation,
re-exports revised LaTeX, and reruns critic/release checks. `safe-repair-report.json` preserves
pre-repair findings, repaired findings, and post-repair warnings; top-level JSON warnings show only
the current post-repair state. It does not retry OpenAI calls, invent citations, create evidence, or
override remaining release blockers.

```bash
uv run factori run-llm-paper \
  --run-id live-prose-repair-smoke-001 \
  --domain "human geography" \
  --llm-scope full-paper \
  --candidate-backend fake \
  --reviewer-backend fake \
  --prose-backend openai \
  --prose-model gpt-5.4-mini \
  --allow-external-calls \
  --max-total-calls 12 \
  --max-prose-calls 12 \
  --max-estimated-cost-usd 1.50 \
  --generate-paper \
  --enable-safe-repair \
  --write-report \
  --json
```

Current candidate-only live smoke:

```bash
uv run factori run-llm-paper \
  --run-id live-candidate-smoke-004 \
  --domain "human geography" \
  --llm-scope candidate-only \
  --candidate-backend openai \
  --candidate-model gpt-5.4-mini \
  --reviewer-backend fake \
  --prose-backend fake \
  --allow-external-calls \
  --max-total-calls 3 \
  --max-candidate-generation-calls 3 \
  --max-estimated-cost-usd 0.20 \
  --write-report \
  --json
```

LLM orchestration artifacts are accounting/context/audit artifacts only. They cannot create or
upgrade evidence labels, turn LLM output into proof/experiment/retrieval evidence, or imply
publication readiness. `--render-check` remains separately gated by `--allow-external-tools`.

Inspect an existing LLM run without rerunning stages, calling APIs, or mutating run artifacts:

```bash
uv run factori inspect-llm-run --run-id live-integrated-openai-smoke-001
uv run factori inspect-llm-run --run-id live-integrated-openai-smoke-001 --json
```

The compact summary reports orchestration and release status, current warnings, call counts,
estimated cost, runtime budget blocks, safe-repair presence, and paper/report artifact paths.

Inspect generated paper bundle artifacts without rerunning stages, calling APIs, or mutating run
artifacts:

```bash
uv run factori inspect-paper-bundle --run-id live-integrated-openai-smoke-001
uv run factori inspect-paper-bundle --run-id live-integrated-openai-smoke-001 --json
```

The compact summary prefers revised Markdown/LaTeX artifacts when present and reports main-body,
appendix, and total heading counts separately, plus word, citation, claim-support, warning,
blocking, safe-repair, and artifact-path details.

Lint generated paper bundle quality without rerunning stages, calling APIs, or mutating run
artifacts:

```bash
uv run factori lint-paper-bundle --run-id live-integrated-openai-smoke-001
uv run factori lint-paper-bundle --run-id live-integrated-openai-smoke-001 --json
uv run factori lint-paper-bundle --run-id live-integrated-openai-smoke-001 \
  --min-words 1500 --min-avg-words-per-section 120 --min-citation-markers 1
```

The lint reports `DraftQualityFailed`, `DraftQualityWarnings`, or `DraftQualityPass` as a draft
quality diagnostic only. It now treats word count as a skeletal-draft proxy and gates primarily on
semantic essentials: problem statement, central contribution, method summary, evidence-boundary
statement, limitations, provenance, absence of fake citations, and absence of fake empirical or
unsupported external factual claims. Fragmentation failures use planned main-body sections and
unplanned headings rather than total heading count; required appendices do not fragment the main
body. Safe repair consolidates central-message text into a planned section, and the deterministic
conclusion fallback remains explicitly non-evidential. Missing citations are a warning when no
retrieval sources are available and no unsupported external facts are claimed. The command does
not change generated-paper release status, safety status, evidence labels, human-review readiness,
or publication-readiness flags. When `claim-support-audit.json` exists, lint also reports missing
required citations, registry scope mismatches, citation placement violations, and
citation-as-validation misuse. Current-run scaffold and absence-of-evidence statements are not
counted as missing-citation failures.

Real OpenAlex retrieval is separately gated and used only for Stage B literature context and
bounded adequacy. It does not prove novelty or complete literature coverage:

```bash
OPENALEX_API_KEY="<key>" uv run factori show-adapters \
  --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
OPENALEX_API_KEY="<key>" uv run factori retrieval-adequacy-demo \
  --query "human geography optimal transport" \
  --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
OPENALEX_API_KEY="<key>" uv run factori run-stage-b \
  --run-id demo --retrieval-backend openalex --allow-external-calls --retrieval-limit 5
```

Real proof verification is separately gated and used only for Stage C mathematical branches. It
requires an explicitly selected local proof backend, external-tool permission, and an executable.
Default runs never execute Lean or any proof tool:

```bash
uv run factori show-adapters \
  --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-stage-c \
  --run-id demo --proof-backend lean --allow-external-tools --proof-executable lean
uv run factori run-all \
  --run-id proof-pipeline --domain "human geography" \
  --proof-backend lean --allow-external-tools --proof-executable lean
```

Local synthetic experiment execution is separately gated and used only for Stage C SyntheticOnly
branches. It requires explicit external-tool permission and an experiment runner. It never supports
real-world empirical validation:

```bash
uv run factori show-adapters \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
uv run factori run-stage-c \
  --run-id demo --experiment-backend local_synthetic \
  --allow-external-tools --experiment-runner local-runner
uv run factori run-all \
  --run-id synthetic-pipeline --domain "human geography" \
  --experiment-backend local_synthetic --allow-external-tools \
  --experiment-runner local-runner
```

`replay-verify` is read-only. With `--write-report`, it writes only non-provenance files under
`runs/demo/replay/` and does not append ledger commits or update the artifact manifest.

`verify-final-release-bundle` is independent replay-by-inspection. `--bundle-path` reads only the
selected bundle; `--run-id` only locates the latest bundle before the same bundle-only checks run.
It never executes recorded commands or modifies the bundle. `--write-report` writes an append-only,
non-ledgered report outside the bundle under the run reports directory.

`diagnose-run` explains available final-audit, release, export, and replay findings. It never
executes its recommended commands. With `--write-report`, it writes only non-provenance files under
`runs/demo/diagnostics/` and does not append ledger commits or update the artifact manifest.

`compare-runs` reads two completed runs and reports deterministic drift and regressions. With
`--write-report`, it writes only non-provenance files under `runs/<candidate>/comparisons/` and
does not append ledger commits or update either artifact manifest.

`status` and `validate-resume` are read-only checkpoint inspection commands. They do not append
ledger commits, update manifests, or write status reports.

`run-all --dry-run` and `plan-run` are read-only planning commands. They mirror run-all stage
ordering and resume validation but do not execute stages, write dry-run reports, append ledger
commits, or update artifact manifests.

`inspect-hygiene` checks manifest/file consistency, artifact hashes, evidence boundaries,
non-provenance report placement, duplicate outputs, stale sidecars/cache files, and unexpected run
contents. It never deletes or repairs files. With `--write-report`, it writes only marked
non-provenance files under `runs/<run_id>/hygiene/`; that directory is excluded from normal
artifact manifests and no ledger commit is created.

`plan-hygiene-remediation` runs the read-only hygiene inspection and maps each finding to a
conservative recommendation with an explicit risk and optional producing-stage rerun command. It
does not execute any recommendation or rewrite a manifest. With `--write-report`, it writes only
marked non-provenance plan files under `runs/<run_id>/hygiene/` and creates no ledger commit.

`critique-paper-shape` is read-only by default. It checks the narrative manuscript contract and
paper-shape diagnostics after `plan-manuscript`. With `--write-report`, it writes hashed, ledgered
manuscript-quality context artifacts under `runs/<run_id>/reports/`; these are not verification
evidence and cannot change scientific labels.

`show-adapters` (alias `adapters`) prints the active registry without invoking it. The default is
`adapter_backend=fake` and `allow_external_calls=false`. The `openai` backend fails before any
network call unless external calls are explicitly allowed and an API key is present.
The `openalex` retrieval backend has the same fail-closed behavior and uses
`OPENALEX_API_KEY`; retrieval artifacts remain non-verification context.
The `lean` proof backend fails before tool execution unless external tools are explicitly allowed
and a proof executable is configured. The `local_synthetic` experiment backend fails before tool
execution unless external tools are explicitly allowed and an experiment runner is configured.
The output also includes provider-neutral capability descriptors for the fake, OpenAI candidate,
OpenAI reviewer, OpenAI prose, OpenAlex retrieval, Lean proof, and local synthetic experiment
backends.

## Foundation and Inspection

One-command deterministic autonomous finalization:

```bash
uv run factori run-autonomous-paper --run-id demo-paper --domain "human geography"
uv run factori run-autonomous-paper --run-id demo-paper --domain "human geography" --resume-existing
uv run factori inspect-autonomous-paper-run --run-id demo-paper
uv run factori inspect-autonomous-paper-checkpoints --run-id demo-paper
uv run factori inspect-autonomous-paper-resume --run-id demo-paper
uv run factori build-scientific-substrate --run-id demo-paper --max-substrates 2
uv run factori inspect-scientific-substrate --run-id demo-paper --json
uv run factori route-substrate-experiment --run-id demo-paper
uv run factori inspect-substrate-experiment-routing --run-id demo-paper --json
uv run factori run-substrate-tournament --run-id demo-paper
uv run factori inspect-substrate-tournament --run-id demo-paper --json
uv run factori plan-creative-mutations --run-id demo-paper --max-mutations 5
uv run factori inspect-creative-mutations --run-id demo-paper --json
uv run factori apply-creative-mutations --run-id demo-paper --max-mutations 3
```

The controller uses local/offline-safe defaults, records all stage outcomes, requires complete
bundle assembly plus independent verification for handoff, and never sets publication readiness.
Resume additionally verifies immutable checkpoint outputs, protocol compatibility, claim/citation
safety, bundle hashes, and ledger lineage before any stage is reused.

```bash
uv run factori init-run --run-id demo
uv run factori show-ledger --run-id demo
uv run factori validate-run --run-id demo
uv run factori questioner-check --run-id demo --candidate-id candidate-001
uv run factori retrieval-adequacy-demo
uv run factori stagnation-demo
```

`build-scientific-substrate` creates context-only scientific planning objects from idea-space
mutation axes. The generated substrates include concrete equations, variables, DGPs, baselines,
measurable hypotheses, result schemas, limitations, and failure modes; they do not create evidence,
validation, or publication readiness.

`plan-creative-mutations` reads the latest IdeaTree, idea-space diagnostics, ScientificSubstrates,
and substrate tournament result to propose context-only next-generation branches.
`apply-creative-mutations` appends selected mutation nodes and ScientificSubstrate candidates for
future bounded experiments; it does not route experiments or create validation evidence.

## Tests and Lint

Canonical `uv` commands:

```bash
uv run pytest
uv run ruff check .
```

Commands for an already-created local environment:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Persistence tests cover atomic replacement, failed-write cleanup, final-byte hashes, newline
normalization, storage protocol conformance, and fixed-clock pipeline timestamps. No separate CLI
configuration is required; normal commands continue to use the UTC system clock.

Default commands do not call external APIs, real Lean, experiment runners, LaTeX tools, Docker, a
server, or a frontend. Only the explicitly gated Stage A OpenAI, Stage B OpenAI reviewer, Stage B
OpenAlex, OpenAI prose, end-to-end LLM orchestration, Stage C Lean/local synthetic, and LaTeX
render-check commands above may call an external API or local external tool.

The gated `uv_local` Python experiment sandbox accepts only approved local bundles, runs a fixed
offline command without shell interpolation, captures `pyproject.toml`, `uv.lock`, fixed seeds,
timeouts, resource limits, stdout/stderr, `metrics.json`, and hashed inputs/outputs. It rejects
network requests, shell commands, path escapes, and dependencies outside the allowlist. Planned
specs and sandbox reports remain workflow context; only a successful result that passes existing
experiment-artifact intake can support its mapped bounded claim.
