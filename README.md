# fActorI

**A multi-fidelity, variance-driven framework for autonomous scientific discovery.**

fActorI aims to automate more of the research process than idea generation or paper writing. Given
a domain, method, or question, it explores a tree of research programs, develops the promising
ones, tests them at increasing levels of rigor, and converges on either a supported contribution or
an explicit account of why the search failed.

The intended output is a **ledgered research object**: a labeled manuscript together with its
candidate tree, literature context, proof or experiment artifacts, failed branches, criticism, and
provenance.

## Lift, Then Filter

fActorI treats discovery as a lift-then-filter process:

    User constraints
          |
          v
    Diverse questions, hypotheses, models, and methods
          |
          v
    Cheap structural and literature checks
          |
          v
    Adversarial review, repair, and pruning
          |
          v
    Deep experiment or proof verification
          |
          v
    Final branch or shared abstraction
          |
          v
    Labeled manuscript and evidence bundle

The system deliberately preserves variance early. Alternative mechanisms, mathematical lenses,
baselines, counterexamples, and verification paths are explored before expensive validation.
Search then becomes progressively stricter, allocating more effort only to branches that survive.

Adaptive controllers may refine a question, strengthen a baseline, repair code, split a proof,
downgrade a claim, stop a stagnant branch, or redirect the remaining budget. Human intervention is
reserved for high-risk, high-cost, ambiguous, or irreversible decisions.

## Evidence Before Narrative

Exploration, verification, and presentation have different authority:

    Proposal     != evidence
    Criticism    != evidence
    Citation     != proof
    Manuscript   != evidence
    LaTeX / PDF  != evidence

Synthetic experiments support only claims within their declared regime. Mathematical claims are
intended to become theorem claims only after formal verification. Unresolved work must remain
labeled as conjecture, negative result, limitation, deferred branch, or failed search.

When several surviving branches share a defensible structure, fActorI attempts to synthesize them
into a common model and attacks that abstraction before using it as the paper nucleus. Otherwise,
it falls back to the strongest supported branch.

## Current State

The prototype implements an end-to-end synthetic experimental path: opportunity discovery,
multi-branch development, literature context, experiment generation and repair, restricted
execution, adversarial criticism, evidence-bounded manuscript synthesis, and hash-linked
provenance.

Its current reliable empirical scope is controlled synthetic research. The generated example below
demonstrates the pipeline; it is not presented as publication-ready science.

**[Example paper](showcase/label-noise-calibration/bundle/paper/final-paper.pdf) |
[Research bundle](showcase/label-noise-calibration/README.md)**

## Models and Providers

The real LLM path currently supports **OpenAI only**. Provider-neutral adapter contracts exist, but
other real LLM providers have not yet been implemented. OpenAlex is used separately for literature
retrieval, while safety checks, execution, metric extraction, provenance validation, and final
assembly are deterministic local operations.

The showcased run split model roles:

| Role | Model |
|---|---|
| Research planning, experiment generation and repair, adaptive questioning, and adjudication | OpenAI gpt-5.6-luna, high reasoning effort |
| Manuscript planning, synthesis, criticism, and revision | OpenAI gpt-5.6-sol, high reasoning effort |

Sol improved the final presentation; it did not create metrics or upgrade experimental evidence.
The full disclosure is retained in the [showcase bundle](showcase/label-noise-calibration/README.md).

## Comparison With AI Scientist-v2

The [Sakana AI Scientist-v2 label-noise paper](https://pub.sakana.ai/ai-scientist-v2/paper/paper.pdf)
is a useful reference point on a closely related subject.

| AI Scientist-v2 example | fActorI example |
|---|---|
| Broader multi-dataset deep-learning study | Narrow controlled synthetic benchmark |
| Stronger conventional paper presentation and figures | Weaker presentation but explicit evidence provenance |
| Annotated evaluation exposes text/figure and claim/evidence inconsistencies | Unsupported generalizations and unresolved uncertainty remain explicit |
| Optimizes strongly for producing a recognizable paper | Optimizes for a traceable research object with bounded claims |

This is an illustrative artifact comparison, not a controlled head-to-head benchmark. The relevant
design hypothesis is that evidence authority and provenance constraints can complement raw model
capability.

## Direction

The next major research tracks are:

- **Lean 4 verification.** Complete the adaptive proof controller, bounded lemma splitting, and a
  reusable theorem registry in which Lean artifacts, not model judgments or prose, provide proof
  authority.
- **Broader empirical work.** Extend the data gate from synthetic-only research to reproducible
  public-data and explicitly gated user-provided-data experiments.
- **Deeper autonomous search.** Improve multi-branch tournaments, research memory, reuse of verified
  lemmas and experiment components, budget allocation, and synthesis of several results into a
  genuine shared abstraction.

The long-term system should terminate honestly with a verified or bounded manuscript, an
informative negative result, a conjecture with explicit obligations, or a failed-search report.

## Quick Start

    uv sync --dev

    uv run factori run-all \
      --run-id demo \
      --domain "synthetic probabilistic classification"

    uv run factori replay-verify --run-id demo

The default path uses deterministic development adapters. External models, retrieval, proof tools,
and experiment runners require explicit gates and budgets.

## Documentation

[Architecture](ARCHITECTURE.md) |
[Context](CONTEXT.md) |
[Commands](COMMANDS.md) |
[Protocols](protocols/README.md) |
[Design specification](fActori_updated_data_regime.tex)

---

**Explore widely, verify selectively, preserve failures, and never let presentation outrun proof.**
