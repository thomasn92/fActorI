"""Deterministic boundary perturbation robustness experiment."""

import json
import math
import random
import statistics
from pathlib import Path


def _fit_alpha(rows: list[dict[str, float]]) -> float:
    denominator = sum(row["log_distance"] ** 2 for row in rows)
    if denominator == 0.0:
        return 1.0
    return -sum(row["log_distance"] * row["log_residual"] for row in rows) / denominator


def _metrics(actual: list[float], predicted: list[float]) -> tuple[float, float]:
    errors = [left - right for left, right in zip(actual, predicted, strict=True)]
    mae = statistics.fmean(abs(error) for error in errors)
    rmse = math.sqrt(statistics.fmean(error * error for error in errors))
    return mae, rmse


def _predict(row: dict[str, float], alpha: float) -> float:
    scale = row["origin_mass"] * row["destination_attractiveness"]
    distance = math.exp(row["log_distance"])
    return scale * distance ** (-alpha)


def _run_setting(config: dict[str, object], setting: dict[str, object]) -> dict[str, object]:
    seed = int(config["seed"]) + int(setting["seed_offset"])
    rng = random.Random(seed)
    n_regions = int(config["n_regions"])
    delta = float(config["distance_offset"])
    noise_level = float(config["noise_level"])
    perturbation = float(setting["perturbation_strength"])
    base_coordinates = [(rng.random(), rng.random()) for _ in range(n_regions)]
    coordinates = [
        (
            min(1.0, max(0.0, x + rng.uniform(-perturbation, perturbation))),
            min(1.0, max(0.0, y + rng.uniform(-perturbation, perturbation))),
        )
        for x, y in base_coordinates
    ]
    origin_mass = [math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)]
    destination_attractiveness = [
        math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)
    ]
    alpha = [1.2 + 0.35 * rng.uniform(-1.0, 1.0) for _ in range(n_regions)]
    rows: list[dict[str, float | int | bool]] = []
    for origin in range(n_regions):
        for destination in range(n_regions):
            if origin == destination:
                continue
            dx = coordinates[origin][0] - coordinates[destination][0]
            dy = coordinates[origin][1] - coordinates[destination][1]
            distance = math.sqrt(dx * dx + dy * dy) + delta
            epsilon = rng.gauss(0.0, noise_level)
            flow = (
                origin_mass[origin]
                * destination_attractiveness[destination]
                * distance ** (-alpha[origin])
                * math.exp(epsilon)
            )
            rows.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "log_distance": math.log(distance),
                    "log_residual": (
                        math.log(flow)
                        - math.log(origin_mass[origin])
                        - math.log(destination_attractiveness[destination])
                    ),
                    "flow": flow,
                    "origin_mass": origin_mass[origin],
                    "destination_attractiveness": destination_attractiveness[destination],
                    "test": (origin * 37 + destination * 11) % 5 == 0,
                }
            )
    train = [row for row in rows if not bool(row["test"])]
    test = [row for row in rows if bool(row["test"])]
    pooled_alpha = _fit_alpha(train)
    origin_alpha = {
        origin: _fit_alpha([row for row in train if int(row["origin"]) == origin])
        for origin in range(n_regions)
    }
    actual = [float(row["flow"]) for row in test]
    baseline_predictions = [_predict(row, pooled_alpha) for row in test]
    method_predictions = [_predict(row, origin_alpha[int(row["origin"])]) for row in test]
    baseline_mae, baseline_rmse = _metrics(actual, baseline_predictions)
    method_mae, method_rmse = _metrics(actual, method_predictions)
    return {
        "setting": str(setting["name"]),
        "baseline_mae": round(baseline_mae, 8),
        "method_mae": round(method_mae, 8),
        "baseline_rmse": round(baseline_rmse, 8),
        "method_rmse": round(method_rmse, 8),
        "mae_improvement": round(baseline_mae - method_mae, 8),
        "rmse_improvement": round(baseline_rmse - method_rmse, 8),
        "sample_count": len(rows),
        "train_pair_count": len(train),
        "test_pair_count": len(test),
        "seed": seed,
        "perturbation_strength": perturbation,
        "noise_level": noise_level,
    }


def main() -> None:
    config = json.loads(Path("experiment_config.json").read_text(encoding="utf-8"))
    table = [_run_setting(config, setting) for setting in config["settings"]]
    original = next(row for row in table if row["setting"] == "original_boundaries")
    perturbed = next(row for row in table if row["setting"] == "perturbed_boundaries")
    original_ratio = (
        (float(original["baseline_mae"]) - float(original["method_mae"]))
        / float(original["baseline_mae"])
    )
    perturbed_ratio = (
        (float(perturbed["baseline_mae"]) - float(perturbed["method_mae"]))
        / float(perturbed["baseline_mae"])
    )
    robustness_ratio = perturbed_ratio / original_ratio if original_ratio > 0.0 else 0.0
    performance_degradation = max(0.0, original_ratio - perturbed_ratio)
    support = all(
        float(row["method_mae"]) < float(row["baseline_mae"])
        and float(row["method_rmse"]) <= float(row["baseline_rmse"])
        for row in table
    )
    metrics = {
        "test_mae_baseline": perturbed["baseline_mae"],
        "test_mae_method": perturbed["method_mae"],
        "test_rmse_baseline": perturbed["baseline_rmse"],
        "test_rmse_method": perturbed["method_rmse"],
        "mae_improvement": perturbed["mae_improvement"],
        "rmse_improvement": perturbed["rmse_improvement"],
        "robustness_ratio": round(robustness_ratio, 8),
        "performance_degradation": round(performance_degradation, 8),
        "sample_count": sum(int(row["sample_count"]) for row in table),
        "train_pair_count": sum(int(row["train_pair_count"]) for row in table),
        "test_pair_count": sum(int(row["test_pair_count"]) for row in table),
        "seed": int(config["seed"]),
        "comparison_table": table,
        "heterogeneity_ablation_present": True,
        "claim_support_satisfied": support,
        "scope": "synthetic boundary perturbation robustness comparison for one mapped claim",
    }
    Path("metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/comparison-table.json").write_text(
        json.dumps(table, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("boundary perturbation distance-decay experiment completed")


if __name__ == "__main__":
    main()
