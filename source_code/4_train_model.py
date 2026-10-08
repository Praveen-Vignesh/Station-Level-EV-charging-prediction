"""
4_train_model.py -- Train and evaluate the forecasting models.

Reads  data/hourly.csv, data/hourly_station.csv, data/stats.json
Writes models/random_forest.joblib
       data/metrics.json          single source of truth for every reported number
       data/predictions.csv       test-set actual vs predicted, for the dashboard
       data/station_results.csv   the same models fitted to the busiest stations

Three iterations are compared on the same chronological hold-out:
  1. baseline  -- repeat the value from the same hour last week (lag_168h)
  2. Linear Regression, and a Random Forest with fixed hyperparameters
  3. Random Forest whose hyperparameters are chosen by cross-validation
     INSIDE the training period only (TimeSeriesSplit), so the test period is
     never used to pick anything.

The final model is the cross-validated Random Forest. That choice is made by the
rule above, not by looking at test scores, even if the fixed-parameter forest
happens to score slightly better on the test period.
"""

import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit

from common import (DATA_DIR, FEATURES, MODEL_DIR, RANDOM_STATE, TARGET,
                    TEST_FRACTION, TOP_N_STATIONS, build_features,
                    chronological_split, read_hourly_csv)

HOURLY_CSV = os.path.join(DATA_DIR, "hourly.csv")
STATION_CSV = os.path.join(DATA_DIR, "hourly_station.csv")
STATS_JSON = os.path.join(DATA_DIR, "stats.json")
METRICS_JSON = os.path.join(DATA_DIR, "metrics.json")
PREDICTIONS_CSV = os.path.join(DATA_DIR, "predictions.csv")
STATION_RESULTS_CSV = os.path.join(DATA_DIR, "station_results.csv")
MODEL_PATH = os.path.join(MODEL_DIR, "random_forest.joblib")

# Fixed hyperparameters used in iteration 2 (the "untuned" forest).
DEFAULT_RF = dict(n_estimators=300, min_samples_leaf=2, random_state=RANDOM_STATE)

PARAM_GRID = {
    "n_estimators": [200, 300],
    "min_samples_leaf": [1, 2, 4],
    "max_depth": [None, 12],
}
CV_SPLITS = 4


def mae(y, p):
    return float(mean_absolute_error(y, p))


def rmse(y, p):
    return float(np.sqrt(mean_squared_error(y, p)))


def improvement(base, value):
    return float((base - value) / base * 100) if base else 0.0


def score(y, p):
    return {"mae": mae(y, p), "rmse": rmse(y, p)}


def fit_and_score(train, test, rf_params):
    """Fit Linear Regression and a Random Forest; return predictions and scores."""
    X_train, y_train = train[FEATURES], train[TARGET]
    X_test, y_test = test[FEATURES], test[TARGET]
    lin = LinearRegression().fit(X_train, y_train)
    rf = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1,
                               **rf_params).fit(X_train, y_train)
    preds = {
        "baseline": test["lag_168h"].to_numpy(),
        "linear": lin.predict(X_test),
        "rf": rf.predict(X_test),
    }
    return lin, rf, preds, {k: score(y_test, v) for k, v in preds.items()}


ABLATION_SETS = [
    ("All six features (final)", FEATURES),
    ("Without lag_1h / lag_2h", ["hour_of_day", "weekday", "lag_24h", "lag_168h"]),
    ("Calendar only (hour, weekday)", ["hour_of_day", "weekday"]),
    ("Daily + weekly lags only", ["lag_24h", "lag_168h"]),
]


def feature_ablation(train, test, rf_params, baseline_mae):
    """Refit the forest on feature subsets to see where the accuracy comes from.

    Hourly demand is rebuilt from sessions using each session's end time, so the
    short lags (1 h, 2 h) can carry a little information about charging that had
    not yet happened. Showing the forest WITHOUT them bounds that effect.
    """
    rows = []
    for name, cols in ABLATION_SETS:
        rf = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1, **rf_params)
        rf.fit(train[cols], train[TARGET])
        pred = rf.predict(test[cols])
        rows.append({"features": name, "columns": cols,
                     "mae": round(mae(test[TARGET], pred), 4),
                     "rmse": round(rmse(test[TARGET], pred), 4),
                     "mae_improvement_pct": round(improvement(baseline_mae, mae(test[TARGET], pred)), 1)})
    return rows


def representative_week(test, rf_pred):
    """Start index of the 168-hour test window whose RF error is closest to the
    overall RF error -- a typical week rather than a flattering one."""
    y = test[TARGET].to_numpy()
    err = np.abs(y - rf_pred)
    overall = err.mean()
    if len(err) <= 168:
        return 0
    window_mae = pd.Series(err).rolling(168).mean().dropna().to_numpy()
    return int(np.argmin(np.abs(window_mae - overall)))


def error_breakdown(test, preds):
    """MAE by hour of day and by weekday for the baseline and the forest."""
    frame = pd.DataFrame({
        "hour_of_day": test["hour_of_day"].to_numpy(),
        "weekday": test["weekday"].to_numpy(),
        "base_err": np.abs(test[TARGET].to_numpy() - preds["baseline"]),
        "rf_err": np.abs(test[TARGET].to_numpy() - preds["rf"]),
        "actual": test[TARGET].to_numpy(),
    })
    out = {}
    for key in ("hour_of_day", "weekday"):
        grouped = frame.groupby(key)[["base_err", "rf_err", "actual"]].mean()
        out["by_" + key] = [
            {key: int(idx), "baseline_mae": round(float(r.base_err), 3),
             "rf_mae": round(float(r.rf_err), 3),
             "mean_demand": round(float(r.actual), 3)}
            for idx, r in grouped.iterrows()
        ]
    return out


def main():
    for path in (HOURLY_CSV, STATION_CSV, STATS_JSON):
        if not os.path.exists(path):
            print("Missing {0}. Run 3_build_hourly.py first.".format(path))
            return 1
    os.makedirs(MODEL_DIR, exist_ok=True)

    hourly = read_hourly_csv(HOURLY_CSV)
    stats = json.load(open(STATS_JSON, encoding="utf-8"))
    train, test = chronological_split(hourly)
    X_train, y_train = train[FEATURES], train[TARGET]
    y_test = test[TARGET]
    print("Rows: {0:,} total | {1:,} train | {2:,} test (chronological {3:.0%}/{4:.0%})".format(
        len(hourly), len(train), len(test), 1 - TEST_FRACTION, TEST_FRACTION))
    print("Train: {0} -> {1}".format(train["hour"].min(), train["hour"].max()))
    print("Test:  {0} -> {1}\n".format(test["hour"].min(), test["hour"].max()))

    # ------------------------------------------- iteration 1 + 2: fixed settings
    lin, rf_default, preds_default, scores_default = fit_and_score(
        train, test, {k: v for k, v in DEFAULT_RF.items() if k != "random_state"})
    base = scores_default["baseline"]["mae"]
    print("Baseline (same hour last week)  MAE {0:6.2f}  RMSE {1:6.2f}".format(
        base, scores_default["baseline"]["rmse"]))
    print("Linear Regression               MAE {0:6.2f}  RMSE {1:6.2f}  ({2:+.1f}%)".format(
        scores_default["linear"]["mae"], scores_default["linear"]["rmse"],
        improvement(base, scores_default["linear"]["mae"])))
    print("Random Forest (fixed params)    MAE {0:6.2f}  RMSE {1:6.2f}  ({2:+.1f}%)".format(
        scores_default["rf"]["mae"], scores_default["rf"]["rmse"],
        improvement(base, scores_default["rf"]["mae"])))

    # ------------------------------------------- iteration 3: cross-validated RF
    print("\nTuning Random Forest on the training period only "
          "(TimeSeriesSplit, {0} folds)...".format(CV_SPLITS))
    t0 = time.time()
    search = GridSearchCV(
        RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1),
        PARAM_GRID,
        cv=TimeSeriesSplit(n_splits=CV_SPLITS),
        scoring="neg_mean_absolute_error",
        n_jobs=1,
    ).fit(X_train, y_train)
    best_params = search.best_params_
    cv_mae = float(-search.best_score_)
    print("Best params {0}  (CV MAE {1:.2f}, {2:.0f}s)".format(
        best_params, cv_mae, time.time() - t0))

    _, rf_tuned, preds_tuned, scores_tuned = fit_and_score(train, test, best_params)
    print("Random Forest (tuned, final)    MAE {0:6.2f}  RMSE {1:6.2f}  ({2:+.1f}%)".format(
        scores_tuned["rf"]["mae"], scores_tuned["rf"]["rmse"],
        improvement(base, scores_tuned["rf"]["mae"])))

    final_rf = rf_tuned
    final_pred = preds_tuned["rf"]

    # ------------------------------------------- analysis of the final model
    importances = sorted(
        ({"feature": f, "importance": round(float(i), 4)}
         for f, i in zip(FEATURES, final_rf.feature_importances_)),
        key=lambda d: -d["importance"])
    breakdown = error_breakdown(test, {"baseline": preds_tuned["baseline"],
                                       "rf": final_pred})
    test_reset = test.reset_index(drop=True)
    week_start = representative_week(test_reset, final_pred)
    week_end = min(week_start + 168, len(test_reset)) - 1

    peak_mask = y_test.to_numpy() >= np.quantile(y_test.to_numpy(), 0.9)
    peak_bias = float(np.mean(final_pred[peak_mask] - y_test.to_numpy()[peak_mask]))
    print("\nMean error on the top-10% busiest test hours: {0:+.2f} kWh/h "
          "(negative = under-prediction)".format(peak_bias))

    ablation = feature_ablation(train, test, best_params, base)
    print("\nFeature ablation (same forest, fewer features):")
    for row in ablation:
        print("  {0:<32} MAE {1:5.2f}  ({2:+.1f}% vs baseline)".format(
            row["features"], row["mae"], row["mae_improvement_pct"]))

    # ------------------------------------------- busiest stations
    long = read_hourly_csv(STATION_CSV)
    rows = []
    for entry in stats["top_stations"][:TOP_N_STATIONS]:
        stn = entry["station_id"]
        series = long[long["station_id"] == stn][["hour", TARGET]]
        feats = build_features(series)
        s_train, s_test = chronological_split(feats)
        _, _, _, s_scores = fit_and_score(s_train, s_test, best_params)
        s_base = s_scores["baseline"]["mae"]
        rows.append({
            "station_id": stn,
            "total_kwh": entry["total_kwh"],
            "zero_share": entry["zero_share"],
            "test_hours": len(s_test),
            "baseline_mae": round(s_base, 4),
            "linear_mae": round(s_scores["linear"]["mae"], 4),
            "rf_mae": round(s_scores["rf"]["mae"], 4),
            "rf_rmse": round(s_scores["rf"]["rmse"], 4),
            "rf_improvement_pct": round(improvement(s_base, s_scores["rf"]["mae"]), 1),
        })
    pd.DataFrame(rows).to_csv(STATION_RESULTS_CSV, index=False)
    print("\nBusiest stations (own model vs own same-hour-last-week baseline):")
    for r in rows:
        print("  {0:<14} zero hours {1:5.1%}  baseline {2:5.2f}  RF {3:5.2f}  ({4:+.1f}%)".format(
            r["station_id"], r["zero_share"], r["baseline_mae"], r["rf_mae"],
            r["rf_improvement_pct"]))

    # ------------------------------------------- write outputs
    joblib.dump(final_rf, MODEL_PATH)

    predictions = pd.DataFrame({
        "hour": test["hour"].dt.strftime("%Y-%m-%d %H:%M").to_numpy(),
        "actual": y_test.to_numpy().round(3),
        "baseline": preds_tuned["baseline"].round(3),
        "linear": preds_tuned["linear"].round(3),
        "rf_fixed": preds_default["rf"].round(3),
        "rf": final_pred.round(3),
    })
    predictions.to_csv(PREDICTIONS_CSV, index=False)

    def pack(name, s):
        return {"name": name, "mae": round(s["mae"], 4), "rmse": round(s["rmse"], 4),
                "mae_improvement_pct": round(improvement(base, s["mae"]), 1)}

    metrics = {
        "split": {
            "train_rows": int(len(train)), "test_rows": int(len(test)),
            "test_fraction": TEST_FRACTION,
            "train_start": train["hour"].min().strftime("%Y-%m-%d %H:%M"),
            "train_end": train["hour"].max().strftime("%Y-%m-%d %H:%M"),
            "test_start": test["hour"].min().strftime("%Y-%m-%d %H:%M"),
            "test_end": test["hour"].max().strftime("%Y-%m-%d %H:%M"),
        },
        "features": FEATURES,
        "models": {
            "baseline": pack("Baseline (same hour last week)", scores_tuned["baseline"]),
            "linear": pack("Linear Regression", scores_tuned["linear"]),
            "rf_fixed": pack("Random Forest (fixed params)", scores_default["rf"]),
            "rf": pack("Random Forest (tuned, final)", scores_tuned["rf"]),
        },
        "default_rf_params": DEFAULT_RF,
        "tuning": {
            "param_grid": PARAM_GRID, "cv": "TimeSeriesSplit(n_splits={0})".format(CV_SPLITS),
            "scoring": "neg_mean_absolute_error", "best_params": best_params,
            "cv_mae": round(cv_mae, 4),
            "tuned_vs_fixed_test_mae": [round(scores_default["rf"]["mae"], 4),
                                        round(scores_tuned["rf"]["mae"], 4)],
        },
        "feature_importance": importances,
        "ablation": ablation,
        "error_breakdown": breakdown,
        "peak_hours_mean_error": round(peak_bias, 3),
        "demo_week": {
            "start": test_reset.loc[week_start, "hour"].strftime("%Y-%m-%d %H:%M"),
            "end": test_reset.loc[week_end, "hour"].strftime("%Y-%m-%d %H:%M"),
            "start_index": week_start, "end_index": int(week_end),
        },
        "dataset": {k: stats[k] for k in (
            "sessions", "stations", "hours", "hours_with_features", "first_hour",
            "last_hour", "total_kwh", "site_zero_share", "site_mean_kwh_per_h",
            "station_zero_share_mean")},
    }
    with open(METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print("\nSaved {0}, {1}, {2}, {3}".format(
        os.path.relpath(MODEL_PATH, os.path.dirname(DATA_DIR)),
        os.path.basename(METRICS_JSON), os.path.basename(PREDICTIONS_CSV),
        os.path.basename(STATION_RESULTS_CSV)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
