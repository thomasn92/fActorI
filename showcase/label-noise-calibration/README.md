# Label-Noise Calibration Showcase

This directory contains the hash-locked final bundle generated from
`label-noise-small-full-004`.

## Paper

**Calibration under Symmetric and Class-Conditional Training-Label Noise: A Bounded Six-Cell
Gaussian-Logistic Benchmark**

- [Rendered PDF](bundle/paper/final-paper.pdf)
- [Markdown manuscript](bundle/paper/final-paper.md)
- [LaTeX source](bundle/paper/final-paper.tex)

The accepted source manuscript was `nucleus-manuscript-revised-0051`. Final assembly remained a
`scientific_draft_with_open_obligations`, with `publication_ready=false`.

## Model Disclosure

- Research planning, experiment generation and repair, adaptive questioning, and adjudication:
  OpenAI `gpt-5.6-luna`, reasoning effort `high`.
- Accepted manuscript planning, synthesis, criticism, and revision: OpenAI `gpt-5.6-sol`,
  reasoning effort `high`.
- Literature retrieval: OpenAlex.
- Safety auditing, execution, metric extraction, provenance validation, assembly, and rendering:
  deterministic local tooling.

Models supplied scientific proposals and prose. They did not have authority to upgrade evidence
labels or create metrics outside executed output.

## Trace

Follow the central result through:

1. [Paper](bundle/paper/final-paper.md)
2. [Claim-to-artifact map](bundle/reports/claim-artifact-map.json)
3. [Evidence-package result](bundle/evidence/evidence-package-result-0005.json)
4. [Sandbox output](bundle/evidence/metrics/evidence-package-sandbox-execution-0002-output.json)
5. [Artifact-bound metric table](bundle/tables/final-paper-artifact-bound-metrics.json)

The generated implementation is not included in this bundle, so the bundle supports metric
provenance but not complete independent reproduction. This limitation is preserved in the paper.

## Integrity

From `showcase/label-noise-calibration/bundle`:

```bash
sha256sum -c reproducibility/hashes.sha256
```

See the [verification report](bundle/reports/verification-report.json),
[provenance manifest](bundle/provenance/provenance-manifest.json), and
[open obligations](bundle/provenance/open-obligations.json) for the bounded status of the result.
