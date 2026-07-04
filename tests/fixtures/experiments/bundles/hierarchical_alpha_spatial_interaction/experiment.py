"""Deterministic hierarchical-alpha spatial interaction experiment."""

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
    n_clusters = int(config["n_clusters"])
    delta = float(config["distance_offset"])
    noise_level = float(config["noise_level"])
    spread = float(setting["cluster_alpha_spread"])
    coordinates = [(rng.random(), rng.random()) for _ in range(n_regions)]
    origin_mass = [math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)]
    destination_attractiveness = [
        math.exp(rng.uniform(-0.2, 0.5)) for _ in range(n_regions)
    ]
    cluster_alpha = [1.25 + spread * (cluster - 1.5) / 1.5 for cluster in range(n_clusters)]
    rows: list[dict[str, float | int | bool]] = []
    for origin in range(n_regions):
        for destination in range(n_regions):
            if origin == destination:
                continue
            cluster = origin % n_clusters
            dx = coordinates[origin][0] - coordinates[destination][0]
            dy = coordinates[origin][1] - coordinates[destination][1]
            distance = math.sqrt(dx * dx + dy * dy) + delta
            epsilon = rng.gauss(0.0, noise_level)
            flow = (
                origin_mass[origin]
                * destination_attractiveness[destination]
                * distance ** (-cluster_alpha[cluster])
                * math.exp(epsilon)
            )
            rows.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "cluster": cluster,
                    "log_distance": math.log(distance),
                    "log_residual": (
                        math.log(flow)
                        - math.log(origin_mass[origin])
                        - math.log(destination_attractiveness[destination])
                    ),
                    "flow": flow,
                    "origin_mass": origin_mass[origin],
                    "destination_attractiveness": destination_attractiveness[destination],
                    "test": (origin * 29 + destination * 13) % 5 == 0,
                }
            )
    train = [row for row in rows if not bool(row["test"])]
    test = [row for row in rows if bool(row["test"])]
    pooled_alpha = _fit_alpha(train)
    cluster_fit = {
        cluster: _fit_alpha([row for row in train if int(row["cluster"]) == cluster])
        for cluster in range(n_clusters)
    }
    origin_fit = {
        origin: _fit_alpha([row for row in train if int(row["origin"]) == origin])
        for origin in range(n_regions)
    }
    actual = [float(row["flow"]) for row in test]
    pooled_predictions = [_predict(row, pooled_alpha) for row in test]
    cluster_predictions = [_predict(row, cluster_fit[int(row["cluster"])]) for row in test]
    full_predictions = [_predict(row, origin_fit[int(row["origin"])]) for row in test]
    pooled_mae, pooled_rmse = _metrics(actual, pooled_predictions)
    cluster_mae, cluster_rmse = _metrics(actual, cluster_predictions)
    full_mae, full_rmse = _metrics(actual, full_predictions)
    return {
        "setting": str(setting["name"]),
        "baseline_mae": round(pooled_mae, 8),
        "method_mae": round(cluster_mae, 8),
        "full_alpha_mae": round(full_mae, 8),
        "baseline_rmse": round(pooled_rmse, 8),
        "method_rmse": round(cluster_rmse, 8),
        "full_alpha_rmse": round(full_rmse, 8),
        "mae_improvement": round(pooled_mae - cluster_mae, 8),
        "rmse_improvement": round(pooled_rmse - cluster_rmse, 8),
        "parameter_count_baseline": 1,
        "parameter_count_method": n_clusters,
        "parameter_count_full": n_regions,
        "complexity_penalized_score": round(
            ((pooled_mae - cluster_mae) / pooled_mae)
            + ((pooled_rmse - cluster_rmse) / pooled_rmse)
            - 0.01 * n_clusters,
            8,
        ),
        "sample_count": len(rows),
        "train_pair_count": len(train),
        "test_pair_count": len(test),
        "seed": seed,
        "cluster_alpha_spread": spread,
        "noise_level": noise_level,
    }


def main() -> None:
    config = json.loads(Path("experiment_config.json").read_text(encoding="utf-8"))
    table = [_run_setting(config, setting) for setting in config["settings"]]
    high = next(row for row in table if row["setting"] == "high_cluster_heterogeneity")
    support = all(
        float(row["method_mae"]) < float(row["baseline_mae"])
        and float(row["method_rmse"]) <= float(row["baseline_rmse"])
        for row in table
    )
    metrics = {
        "test_mae_baseline": high["baseline_mae"],
        "test_mae_method": high["method_mae"],
        "test_rmse_baseline": high["baseline_rmse"],
        "test_rmse_method": high["method_rmse"],
        "mae_improvement": high["mae_improvement"],
        "rmse_improvement": high["rmse_improvement"],
        "parameter_count_baseline": high["parameter_count_baseline"],
        "parameter_count_method": high["parameter_count_method"],
        "complexity_penalized_score": high["complexity_penalized_score"],
        "sample_count": sum(int(row["sample_count"]) for row in table),
        "train_pair_count": sum(int(row["train_pair_count"]) for row in table),
        "test_pair_count": sum(int(row["test_pair_count"]) for row in table),
        "seed": int(config["seed"]),
        "comparison_table": table,
        "heterogeneity_ablation_present": True,
        "claim_support_satisfied": support,
        "scope": "synthetic hierarchical-alpha OD-flow comparison for one mapped claim",
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
    print("hierarchical alpha spatial interaction experiment completed")


if __name__ == "__main__":
    main()
