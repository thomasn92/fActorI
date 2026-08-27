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
