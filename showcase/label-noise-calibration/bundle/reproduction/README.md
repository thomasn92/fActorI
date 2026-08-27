# Reproduction Context

- `uv run factori inspect-final-paper --run-id label-noise-small-full-004 --json`
- `uv run factori verify-final-paper --run-id label-noise-small-full-004`
- `uv run factori render-final-paper --run-id label-noise-small-full-004 --allow-external-tools --latex-executable pdflatex`
- `uv run factori build-final-paper-bundle --run-id label-noise-small-full-004`
- `Inspect metric sources listed in runs/label-noise-small-full-004/reports/final-paper-provenance-manifest-0055.json.`
- `The bundle does not guarantee full scientific reproducibility when upstream LLM or retrieval calls are required.`

Verification status at bundle creation: `verified_with_warnings`.
No claim of full scientific reproducibility, publication readiness, or external validation is made.
