"""Approved deterministic synthetic calibration fixture."""

import json
import random
import statistics
from pathlib import Path


def main() -> None:
    config = json.loads(Path("experiment_config.json").read_text(encoding="utf-8"))
    seed = int(config["seed"])
    random.seed(seed)
    targets = [index / 20 for index in range(1, 20)]
    noise = [random.uniform(-0.04, 0.04) for _ in targets]
    baseline = [
        min(1.0, max(0.0, target + delta))
        for target, delta in zip(targets, noise, strict=True)
    ]
    method = [
        min(1.0, max(0.0, target + delta * 0.55))
        for target, delta in zip(targets, noise, strict=True)
    ]
    baseline_mae = statistics.fmean(
        abs(value - target) for value, target in zip(baseline, targets, strict=True)
    )
    method_mae = statistics.fmean(
        abs(value - target) for value, target in zip(method, targets, strict=True)
    )
    metrics = {
        "seed": seed,
        "sample_count": len(targets),
        "baseline_mae": round(baseline_mae, 8),
        "method_mae": round(method_mae, 8),
        "bounded_improvement": round(baseline_mae - method_mae, 8),
        "scope": "synthetic calibration fixture for one mapped result claim",
    }
    Path("metrics.json").write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")
    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/summary.json").write_text(
        json.dumps({"configuration": config, "metrics": metrics}, sort_keys=True),
        encoding="utf-8",
    )
    print("synthetic calibration fixture completed")


if __name__ == "__main__":
    main()
