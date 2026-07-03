"""Deterministic stdlib-only PCA/low-rank OD residual experiment."""

import json
import math
import random
import statistics
from pathlib import Path


def _fit_alpha(rows: list[dict[str, float]]) -> float:
    denominator = sum(row["log_distance"] ** 2 for row in rows)
    if denominator == 0.0:
        return 1.0
    return -sum(row["log_distance"] * row["log_scaled_flow"] for row in rows) / denominator


def _metrics(actual: list[float], predicted: list[float]) -> tuple[float, float]:
    errors = [left - right for left, right in zip(actual, predicted, strict=True)]
    mae = statistics.fmean(abs(error) for error in errors)
    rmse = math.sqrt(statistics.fmean(error * error for error in errors))
    return mae, rmse


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return [1.0 / math.sqrt(len(values)) for _ in values]
    return [value / norm for value in values]


def _rank_one(
    matrix: list[list[float]], iterations: int = 32
) -> tuple[list[float], list[float], float]:
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if matrix else 0
    v = [1.0 / math.sqrt(n_cols) for _ in range(n_cols)]
    u = [1.0 / math.sqrt(n_rows) for _ in range(n_rows)]
    for _ in range(iterations):
        u = _normalize([sum(matrix[i][j] * v[j] for j in range(n_cols)) for i in range(n_rows)])
        v = _normalize([sum(matrix[i][j] * u[i] for i in range(n_rows)) for j in range(n_cols)])
    singular = sum(u[i] * matrix[i][j] * v[j] for i in range(n_rows) for j in range(n_cols))
    return u, v, singular


def _correlation(left: list[float], right: list[float]) -> float:
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0.0:
        return 0.0
    return abs(sum(a * b for a, b in zip(left_centered, right_centered, strict=True)) / denominator)


def _run_setting(config: dict[str, object], setting: dict[str, object]) -> dict[str, object]:
    seed = int(config["seed"]) + int(setting["seed_offset"])
    rng = random.Random(seed)
    n_regions = int(config["n_regions"])
    delta = float(config["distance_offset"])
    noise_level = float(config["noise_level"])
    pooled_alpha_true = float(config["pooled_alpha"])
    strength = float(setting["factor_strength"])
    coordinates = [(rng.random(), rng.random()) for _ in range(n_regions)]
    origin_mass = [math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)]
    destination_attractiveness = [
        math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)
    ]
    origin_factor = [rng.uniform(-1.0, 1.0) for _ in range(n_regions)]
    destination_factor = [rng.uniform(-1.0, 1.0) for _ in range(n_regions)]
    rows: list[dict[str, float | int | bool]] = []
    for origin in range(n_regions):
        for destination in range(n_regions):
            if origin == destination:
                continue
            dx = coordinates[origin][0] - coordinates[destination][0]
            dy = coordinates[origin][1] - coordinates[destination][1]
            distance = math.sqrt(dx * dx + dy * dy) + delta
            latent_residual = strength * origin_factor[origin] * destination_factor[destination]
            epsilon = rng.gauss(0.0, noise_level)
            flow = (
                origin_mass[origin]
                * destination_attractiveness[destination]
                * distance ** (-pooled_alpha_true)
                * math.exp(latent_residual + epsilon)
            )
            rows.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "log_distance": math.log(distance),
                    "log_scaled_flow": (
                        math.log(flow)
                        - math.log(origin_mass[origin])
                        - math.log(destination_attractiveness[destination])
                    ),
                    "flow": flow,
                    "origin_mass": origin_mass[origin],
                    "destination_attractiveness": destination_attractiveness[destination],
                    "test": (origin * 19 + destination * 23) % 5 == 0,
                }
            )
    train = [row for row in rows if not bool(row["test"])]
    test = [row for row in rows if bool(row["test"])]
    alpha_hat = _fit_alpha(train)
    residual_matrix = [[0.0 for _ in range(n_regions)] for _ in range(n_regions)]
    train_mask = [[False for _ in range(n_regions)] for _ in range(n_regions)]
    for row in train:
        origin = int(row["origin"])
        destination = int(row["destination"])
        scale = float(row["origin_mass"]) * float(row["destination_attractiveness"])
        distance = math.exp(float(row["log_distance"]))
        baseline = scale * distance ** (-alpha_hat)
        residual_matrix[origin][destination] = math.log(float(row["flow"]) + 1e-9) - math.log(
            baseline + 1e-9
        )
        train_mask[origin][destination] = True
    u, v, singular = _rank_one(residual_matrix)
    actual: list[float] = []
    baseline_predictions: list[float] = []
    method_predictions: list[float] = []
    for row in test:
        origin = int(row["origin"])
        destination = int(row["destination"])
        scale = float(row["origin_mass"]) * float(row["destination_attractiveness"])
        distance = math.exp(float(row["log_distance"]))
        baseline = scale * distance ** (-alpha_hat)
        correction = singular * u[origin] * v[destination]
        actual.append(float(row["flow"]))
        baseline_predictions.append(baseline)
        method_predictions.append(baseline * math.exp(correction))
    baseline_mae, baseline_rmse = _metrics(actual, baseline_predictions)
    method_mae, method_rmse = _metrics(actual, method_predictions)
    residual_values: list[float] = []
    residual_errors: list[float] = []
    for origin in range(n_regions):
        for destination in range(n_regions):
            if not train_mask[origin][destination]:
                continue
            actual_residual = residual_matrix[origin][destination]
            predicted_residual = singular * u[origin] * v[destination]
            residual_values.append(actual_residual)
            residual_errors.append(actual_residual - predicted_residual)
    residual_variance = statistics.fmean(value * value for value in residual_values)
    error_variance = statistics.fmean(value * value for value in residual_errors)
    explained = max(0.0, 1.0 - error_variance / residual_variance) if residual_variance else 0.0
    return {
        "setting": str(setting["name"]),
        "baseline_mae": round(baseline_mae, 8),
        "method_mae": round(method_mae, 8),
        "baseline_rmse": round(baseline_rmse, 8),
        "method_rmse": round(method_rmse, 8),
        "mae_improvement": round(baseline_mae - method_mae, 8),
        "rmse_improvement": round(baseline_rmse - method_rmse, 8),
        "latent_factor_recovery_correlation": round(_correlation(u, origin_factor), 8),
        "explained_residual_variance": round(explained, 8),
        "sample_count": len(rows),
        "train_pair_count": len(train),
        "test_pair_count": len(test),
        "seed": seed,
        "latent_factor_strength": strength,
        "noise_level": noise_level,
    }


def main() -> None:
    config = json.loads(Path("experiment_config.json").read_text(encoding="utf-8"))
    table = [_run_setting(config, setting) for setting in config["settings"]]
    high = next(row for row in table if row["setting"] == "high_latent_factor_strength")
    low = next(row for row in table if row["setting"] == "low_latent_factor_strength")
    support = all(
        float(row["method_mae"]) < float(row["baseline_mae"])
        and float(row["method_rmse"]) <= float(row["baseline_rmse"])
        and float(row["latent_factor_recovery_correlation"]) > 0.0
        for row in table
    )
    metrics = {
        "test_mae_baseline": high["baseline_mae"],
        "test_mae_method": high["method_mae"],
        "test_rmse_baseline": high["baseline_rmse"],
        "test_rmse_method": high["method_rmse"],
        "mae_improvement": high["mae_improvement"],
        "rmse_improvement": high["rmse_improvement"],
        "latent_factor_recovery_correlation": high["latent_factor_recovery_correlation"],
        "explained_residual_variance": high["explained_residual_variance"],
        "sample_count": sum(int(row["sample_count"]) for row in table),
        "train_pair_count": sum(int(row["train_pair_count"]) for row in table),
        "test_pair_count": sum(int(row["test_pair_count"]) for row in table),
        "seed": int(config["seed"]),
        "comparison_table": table,
        "heterogeneity_ablation_present": True,
        "high_heterogeneity_advantage_exceeds_low": (
            float(high["mae_improvement"]) > float(low["mae_improvement"])
        ),
        "claim_support_satisfied": support,
        "scope": "synthetic PCA low-rank OD residual comparison for one mapped claim",
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
    print("pca low-rank OD residual experiment completed")


if __name__ == "__main__":
    main()
