# Calibration under Symmetric and Class-Conditional Training-Label Noise: A Bounded Six-Cell Gaussian-Logistic Benchmark

## Abstract

This bounded artifact-summary report asks whether the artifact-reported mean paired differences from uncalibrated logistic regression have one direction in the two zero-noise cells and the opposite direction in the four reported nonzero-noise cells. The execution record describes a six-cell synthetic Gaussian-logistic benchmark of temperature scaling and beta calibration under symmetric and class-conditional training-label corruption, with clean calibration labels and independently generated clean evaluation [Exec]. For the field labeled as calibrated-method clean-posterior Brier risk minus the uncalibrated-logistic value, both calibrated methods have positive reported means in cells 0 and 3 and negative reported means in cells 1, 2, 4, and 5. This is a cellwise descriptive pattern, not a pooled contrast or an uncertainty-supported claim of reliably nonzero effects. The displayed fields are relative and do not provide absolute primary-risk levels or establish practical effect magnitude. Interval construction, implementation materials, and broader repetition-level outputs remain unavailable. The negative control is inconclusive and is not used to support this abstract or the reported pattern.

## 1. Motivation and study question

This report concerns a controlled synthetic benchmark comparing uncalibrated logistic regression with two authorized post-hoc methods, temperature scaling and beta calibration [Exec]. The central descriptive question is whether the two calibrated methods' reported cellwise mean differences from the uncalibrated baseline are positive in each zero-noise cell and negative in each reported nonzero-noise cell.

The question concerns six separate cellwise contrasts. It does not define or estimate a pooled zero-noise versus nonzero-noise effect. The report makes no claim about a general calibration rule, methods outside the authorized comparison, real datasets, deployment settings, real-world validation, theorem verification, novelty, underuse, publication readiness, or general domain truth.

## 2. Bounded literature context

The retrieved literature places calibration near several materially different problems: estimation with corrupted soft labels and calibration, [Ryota Ushio and Takashi Ishida et al., 2025](#RetrievedW4415036265) application-specific noise filtering followed by isotonic calibration, [Yiyong Pan and Xilai Jia et al., 2026](#RetrievedW7154992691) calibration and reranking in multi-label reduction, [Cheng Li, 2019](#RetrievedW3003430727) graph-model calibration under distribution shift, [Abderaouf Bahi, 2026](#RetrievedW7168433868) and classification calibration of a tunable loss studied with label-flip experiments. [Tyler Sypherd and Mario Dı́az et al., 2019](#RetrievedW4288335971) These works differ from the present binary Gaussian-logistic design in targets, models, data settings, or calibration roles. They therefore provide adjacent context rather than direct comparators or numerical support for this benchmark. The supplied retrieval is bounded and does not establish complete coverage, priority, novelty, or underuse.

**Literature takeaway.** The retrieved sources show that calibration is studied in heterogeneous settings, but none supplies the cellwise benchmark contrast examined here. With that boundary established, the next section fixes the executed design and reporting convention.

## 3. Design and reporting convention

Unless otherwise noted, the design and reporting fields in this section are transcribed from the execution artifact [Exec].

### 3.1 Prespecified cells

The artifact describes a balanced Gaussian-logistic data-generating process with six cells and 20 repetitions per cell. Cells 0–2 form the symmetric branch, and cells 3–5 form the class-conditional alpha/beta branch. Training-label corruption, clean calibration labels, and independently generated clean evaluation are execution-reported procedures rather than independently audited implementation findings [Exec].

| Cell | Design branch | Reported target parameterization |
|---:|---|---|
| 0 | Symmetric | nominal rate 0; alpha 0, beta 0 |
| 1 | Symmetric | nominal rate 0.1; alpha 0.1, beta 0.1 |
| 2 | Symmetric | nominal rate 0.3; alpha 0.3, beta 0.3 |
| 3 | Class-conditional | alpha 0, beta 0 |
| 4 | Class-conditional | alpha 0.05, beta 0.15 |
| 5 | Class-conditional | alpha 0.15, beta 0.45 |

*Table 1 note.* Design fields are transcribed from [Exec]. Nominal targets are formatted to reader-appropriate decimal precision; exact persisted serialization remains in the bound artifact.

The artifact reports realized-rate audit fields, but complete repetition-level records are not exposed in the supplied materials. Those materials also do not define which class transition alpha or beta denotes. The labels and target values are therefore retained without assigning transition meanings or deriving marginal noise rates [Exec].

Cells 0 and 3 belong to separate prespecified branches but share the same zero-noise target. They are not different noise magnitudes and do not extend the number of represented noise levels.

**Design takeaway.** The benchmark contrasts two parameterization branches while retaining the same noise magnitude in the two zero-noise cells. This structure motivates cellwise reporting rather than a pooled branch or noise-group comparison.

### 3.2 Artifact-reported paired-difference field

For method *m*, cell *c*, and repetition *r*, let \(d^{\mathrm{art}}_{m,c,r}\) denote the execution field labeled as the calibrated method's clean-posterior Brier-risk value minus the corresponding uncalibrated-logistic value. The displayed field is

\[
\bar d^{\mathrm{art}}_{m,c}
= \operatorname{artifact\text{-}reported\ mean}_{r=1}^{20}
  d^{\mathrm{art}}_{m,c,r}.
\]

This notation transcribes the artifact's pairing and mean field; it is not an independently audited definition of the underlying risk aggregation [Exec]. A negative field indicates a lower reported value than the uncalibrated-logistic comparator, while a positive field indicates a higher reported value.

The artifact also contains endpoints labeled as 95% interval fields, but their construction and coverage are not established by the supplied payload. They are omitted from the claim-bearing display rather than presented as ordinary confidence intervals. Clean-posterior Brier risk is not treated as interchangeable with empirical clean-label Brier score, log loss, ECE, reliability-curve deviation, AUROC, or accuracy.

## 4. Cellwise primary results

Table 2 retains the complete set of primary mean paired-difference fields for the two calibrated methods across the six cells. The wider all-metric payload, raw repetition-level results, interval construction, and pooled reconciliation fields remain unavailable.

| Cell | Temperature scaling: artifact-reported mean difference | Beta calibration: artifact-reported mean difference |
|---:|---:|---:|
| 0 | 0.000151345001012707 | 0.00047639548159566564 |
| 1 | -0.001501968910291359 | -0.0013122284366247374 |
| 2 | -0.013475293198599395 | -0.013463315191771453 |
| 3 | 0.00016190590637167883 | 0.00050354208170532 |
| 4 | -0.0004155128598029395 | -0.0036593250513843475 |
| 5 | -0.0060240929547245164 | -0.0366280226527847 |

*Table 2 note.* Fields are relative to uncalibrated logistic regression. Negative fields indicate lower reported primary values, and positive fields indicate higher reported primary values. The metric tokens materialize the exact persisted artifact values; their digits represent artifact transcription precision, not independently justified scientific or inferential precision. Formal support: [Exec].

Cell by cell, both calibrated methods have positive mean fields in cells 0 and 3 and negative mean fields in cells 1, 2, 4, and 5 [Exec]. This answers the descriptive question at the contrast level represented by the evidence. It is not a pooled zero-noise versus nonzero-noise estimate, a ranking between the two calibrated methods, or a claim about any other metric.

The table reports relative fields and does not expose absolute primary-risk levels. It therefore does not establish practical effect magnitude. Moreover, signs of repetition means do not establish statistically distinguishable or reliably nonzero effects because the interval construction and coverage remain unresolved.

No pooled method table is reproduced or interpreted. Although the artifact contains pooled fields, their weighting and reconciliation rule is unresolved; no pooled ordering or aggregation-based conclusion is asserted.

**Results takeaway.** The supported answer is the six-cell sign pattern relative to the uncalibrated baseline. It is descriptive, relative, and neither pooled nor uncertainty-supported. The controls next delimit what can be inferred from that pattern.

## 5. Controls, diagnostics, and limitations

The following paragraphs transcribe distinct control and diagnostic clusters from the execution record; none constitutes independent validation evidence.

For each authorized method, the execution summary records all attempted fits as valid, with no failed fits or reported optimization failures [Exec]. These are run-level status fields, not scientific validation.

The zero-noise control summary aggregates method-cell repetitions across cells 0 and 3 rather than reporting its aggregate count separately for each cell. Its status field is described neutrally and does not establish either zero-noise cell as a successful or failed validation control [Exec].

A limited rerun covered repetitions 0 and 1 in cells 0 and 5. The artifact records valid cases, maximum differences below its stated thresholds, and a status field labeled as passed. Because only that subset was rerun and the implementation package is unavailable, the record is not whole-benchmark validation [Exec].

The post-hoc AUROC diagnostic records valid pairs, deltas below the artifact's stated tolerance, and a status field labeled as passed. This is an execution-reported diagnostic and does not support a general ranking-preservation or calibration claim [Exec].

The permuted-calibration negative control used within-split deterministic permutation. Its aggregate Brier-difference ranges include negative and positive fields, and its status field is labeled as passed. Its interpretation is **inconclusive** because the supplied evidence does not expose the needed sign convention, cellwise and repetition-level threshold crossings, or a decision rule connecting those records to the status field [Exec]. The status is therefore reported neutrally. This control does not support robustness, validation, the abstract, the results takeaway, or the conclusion.

The supplied materials do not include implementation code, configuration and seed manifests, plots, unit-test outputs, the broader all-metric and repetition-level output, pooled reconciliation, interval construction, or alpha/beta transition semantics [Exec]. The completed evidence-package execution summarized here is distinct from the unavailable self-contained reproducibility package. These implementation limitations prevent independent audit without changing the report's artifact-summary genre.

**Controls takeaway.** The status and diagnostic fields describe the recorded execution. The negative control remains inconclusive, the rerun is limited, and none of these controls supplies robustness or validation language. The remaining obligations are therefore stated as future work rather than completed findings.

## 6. Unresolved obligations for future verification

The following obligations remain unresolved and do not alter the current bounded result:

1. Confirm that the exposed cell-level tables reconcile exactly with the pooled summaries.
2. Investigate any negative-control threshold crossings before treating that diagnostic as a robustness confirmation.
3. Verify that conclusions do not compare against excluded noise-correction methods or generalize beyond the specified DGP and clean-calibration setting.
4. Generate and execute the unavailable self-contained artifact within the authorized resource ceilings, distinct from the completed evidence-package execution summarized here.
5. For any future rerun, reproduction, or verification execution, pre-lock seeds, optimization, clipping, invalid-fit, binning, and interval rules before inspecting its outcomes.
6. Verify all six cells, controls, negative controls, required tables, plots, manifests, audits, and machine-readable outputs.
7. Resolve or explicitly report any failed repetitions, implementation discrepancies, or realized-noise deviations.
8. For any future verification artifact, assign evidence labels only after that execution and preserve the bounded claim scope regardless of outcome.

## 7. Bounded conclusion

Within the specified balanced Gaussian-logistic benchmark, both authorized calibrated methods have positive artifact-reported primary mean differences from uncalibrated logistic regression in cells 0 and 3 and negative mean differences in cells 1, 2, 4, and 5 [Exec]. Cells 0 and 3 are separate design branches at the same zero-noise magnitude. The answer is cellwise and descriptive, conditional on the reported design, clean calibration condition, independently generated clean evaluation, retained alpha/beta parameterization, and artifact-defined reporting convention.

This sign pattern is not a pooled estimand and, without interpretable intervals or absolute primary-risk levels, does not establish reliably nonzero effects or practical magnitude. It does not automatically apply to other metrics, methods, models, data-generating processes, real datasets, or deployment settings. The inconclusive negative control, limited rerun, unavailable broader output, unresolved pooled reconciliation, unavailable implementation package, and unresolved alpha/beta transition semantics provide no additional support for the conclusion.

## Provenance note and references

[Exec] *SyntheticExperimentEvidence* execution artifact, `evidence-package-result-0005`. Reader-facing citations use this short identifier; the full source path and formal binding identifier are confined to the machine-readable provenance note below.

- Ryota Ushio, Takashi Ishida, and Masashi Sugiyama. *Practical estimation of the optimal classification error with soft labels and calibration*. arXiv, 2025. DOI: `10.48550/arxiv.2505.20761`.
- Yiyong Pan, Xilai Jia, Jieru Huang, Gen Li, and Pengyu Xu. *Injury Severity Prediction for Older Driver Accidents via Denoised Cascade Framework and Probability Calibration*. *World Electric Vehicle Journal*, 2026. DOI: `10.3390/wevj17040219`.
- Cheng Li. *Reduction methods for multi-label classification*. Thesis, 2019. DOI: `10.17760/d20328153`.
- Abderaouf Bahi. *When does distribution shift break graph neural networks calibration?* arXiv, 2026. Source identifier: `W7168433868`.
- Tyler Sypherd, Mario Díaz, John Kevin Cava, Gautam Dasarathy, Peter Kairouz, and Lalitha Sankar. *A Tunable Loss Function for Robust Classification: Calibration, Landscape, and Generalization*. arXiv, 2019. DOI: `10.48550/arxiv.1906.02314`.

### Machine-readable claim map

```json
{
  "claim_id": "claim-primary-hybrid-evidence-package-0001-001-llm-substrate-0001-llm-variant-0001-deep-opportunity-0001-synthetic-probabilisti",
  "artifact_id": "evidence-package-result-0005",
  "evidence_label": "SyntheticExperimentEvidence",
  "artifact_citation_key": "Exec",
  "binding_id": "evidence-citation-hybrid-evidence-package-0001-001-llm-substrate-0001-llm-variant-0001-deep-opportunity-0001-synthetic-probabilisti-001",
  "source": "runs/label-noise-small-full-004/experiments/evidence-package-sandbox-execution-0002-output.json",
  "metric_tokens": [
    "0.00047639548159566564",
    "-0.0013122284366247374",
    "-0.013463315191771453",
    "0.00050354208170532",
    "-0.0036593250513843475",
    "-0.0366280226527847",
    "0.000151345001012707",
    "-0.001501968910291359",
    "-0.013475293198599395",
    "0.00016190590637167883",
    "-0.0004155128598029395",
    "-0.0060240929547245164"
  ],
  "contrast_scope": "separate cellwise paired-difference fields relative to uncalibrated logistic regression",
  "uncertainty_scope": "interval construction and coverage unresolved; no reliably-nonzero inference",
  "absolute_risk_scope": "absolute primary-risk levels unavailable; practical magnitude not established",
  "negative_control_status": "inconclusive and not supporting the abstract, conclusion, robustness, or validation",
  "validation_status": "not verification evidence"
}
```

## Assembly Appendices

### Open Obligations and Scope Boundaries
- Confirm that the exposed cell-level tables reconcile exactly with the pooled summaries.
- Investigate any negative-control threshold crossings before treating that diagnostic as a robustness confirmation.
- Verify that conclusions do not compare against excluded noise-correction methods or generalize beyond the specified DGP and clean-calibration setting.
- Generate and execute the self-contained artifact within the authorized resource ceilings.
- Pre-lock seeds, optimization, clipping, invalid-fit, binning, and interval rules before inspecting outcomes.
- Verify all six cells, controls, negative controls, required tables, plots, manifests, audits, and machine-readable outputs.
- Resolve or explicitly report any failed repetitions, implementation discrepancies, or realized-noise deviations.
- Assign evidence labels only after execution and preserve the bounded claim scope regardless of outcome.
- Make the complete cell-by-method result table and all required audit artifacts directly inspectable.
- Resolve the negative-control sign convention, threshold application, and pass/fail/inconclusive rule with cellwise and repetition-level reporting.
- Clarify the nominal class-conditional rate estimand and report its alpha/beta and marginal-rate implications.
- Replace undefined practical language such as meaningful differences or define prespecified practical-effect thresholds and multiplicity handling.
- Preserve the zero-noise sign reversal and the limited rerun scope in the manuscript framing.

### Provenance and Reproducibility Context
- Artifact bindings, execution records, and reproduction context are listed in the paper manifest.

## References

- <a id="RetrievedW4415036265"></a>Ryota Ushio, Takashi Ishida, Masashi Sugiyama (2025). Practical estimation of the optimal classification error with soft labels and calibration. `10.48550/arxiv.2505.20761`
- <a id="RetrievedW7154992691"></a>Yiyong Pan, Xilai Jia, Jieru Huang, Gen Li, Pengyu Xu (2026). Injury Severity Prediction for Older Driver Accidents via Denoised Cascade Framework and Probability Calibration. `10.3390/wevj17040219`
- <a id="RetrievedW3003430727"></a>Cheng Li (2019). Reduction methods for multi-label classification. `10.17760/d20328153`
- <a id="RetrievedW7168433868"></a>Abderaouf Bahi (2026). When does distribution shift break graph neural networks calibration?. `W7168433868`
- <a id="RetrievedW4288335971"></a>Tyler Sypherd, Mario Dı́az, John Kevin Cava, Gautam Dasarathy, Peter Kairouz, Lalitha Sankar (2019). A Tunable Loss Function for Robust Classification: Calibration, Landscape, and Generalization. `10.48550/arxiv.1906.02314`
