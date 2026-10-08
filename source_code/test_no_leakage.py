"""
test_no_leakage.py -- Prove that no future information reaches the model.

Run:  python test_no_leakage.py     (plain asserts, no pytest needed)

Checks
  1. The hourly series is gap-free, so "shift(k)" really means "k hours earlier".
  2. Every lag feature equals demand at (hour - k), looked up by timestamp rather
     than by row position -- independently of how build_features() computes it.
  3. The train period ends strictly before the test period begins.
  4. The checker itself works: a deliberately leaky feature (shift(-1)) is caught.
  5. Control: a forest trained on SHUFFLED targets is clearly worse than the real
     forest, so the real model's accuracy comes from learned signal and not from
     row order or an accidental copy of the target.
"""

import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from common import (DATA_DIR, FEATURES, LAGS, RANDOM_STATE, TARGET,
                    build_features, chronological_split, read_hourly_csv)

SITE_CSV = os.path.join(DATA_DIR, "hourly_site.csv")


def lag_violations(features, site, lags=LAGS):
    """Count rows where lag_Kh != demand at (hour - K), matched by timestamp."""
    by_hour = site.set_index("hour")[TARGET]
    bad = 0
    for k in lags:
        expected = by_hour.reindex(features["hour"] - pd.Timedelta(hours=k)).to_numpy()
        actual = features["lag_{0}h".format(k)].to_numpy()
        bad += int((~np.isclose(expected, actual, equal_nan=False)).sum())
    return bad


def main():
    if not os.path.exists(SITE_CSV):
        print("Missing {0}. Run 3_build_hourly.py first.".format(SITE_CSV))
        return 1

    site = read_hourly_csv(SITE_CSV)
    features = build_features(site)

    # 1. gap-free hourly series
    steps = site["hour"].diff().dropna().unique()
    assert len(steps) == 1 and steps[0] == pd.Timedelta(hours=1), \
        "hourly series has gaps or duplicates: {0}".format(steps)
    print("PASS 1  hourly series is gap-free ({0:,} consecutive hours)".format(len(site)))

    # 2. every lag looks strictly backward
    assert TARGET not in FEATURES, "target is listed as a model feature"
    bad = lag_violations(features, site)
    assert bad == 0, "{0} lag values do not match demand at (hour - k)".format(bad)
    print("PASS 2  all {0} lag columns equal demand at (hour - k) for all "
          "{1:,} rows".format(len(LAGS), len(features)))

    # 3. chronological split
    train, test = chronological_split(features)
    assert train["hour"].max() < test["hour"].min(), "train and test periods overlap"
    assert train["hour"].is_monotonic_increasing and test["hour"].is_monotonic_increasing
    print("PASS 3  train ends {0} < test starts {1}".format(
        train["hour"].max().strftime("%Y-%m-%d %H:%M"),
        test["hour"].min().strftime("%Y-%m-%d %H:%M")))

    # 4. the checker catches a real leak
    leaky = features.copy()
    leaky["lag_1h"] = site.set_index("hour")[TARGET].reindex(
        leaky["hour"] + pd.Timedelta(hours=1)).to_numpy()   # NEXT hour's demand
    assert lag_violations(leaky.dropna(), site) > 0, "checker failed to detect a leak"
    print("PASS 4  checker detects a deliberately leaky feature (shift(-1))")

    # 5. shuffled-target control
    params = dict(n_estimators=200, min_samples_leaf=2, random_state=RANDOM_STATE,
                  n_jobs=-1)
    real = RandomForestRegressor(**params).fit(train[FEATURES], train[TARGET])
    real_mae = mean_absolute_error(test[TARGET], real.predict(test[FEATURES]))
    shuffled_y = np.random.default_rng(RANDOM_STATE).permutation(train[TARGET].to_numpy())
    control = RandomForestRegressor(**params).fit(train[FEATURES], shuffled_y)
    control_mae = mean_absolute_error(test[TARGET], control.predict(test[FEATURES]))
    assert control_mae > 1.2 * real_mae, \
        "shuffled control ({0:.2f}) is not clearly worse than real ({1:.2f})".format(
            control_mae, real_mae)
    print("PASS 5  real forest MAE {0:.2f} vs shuffled-target control MAE {1:.2f}".format(
        real_mae, control_mae))

    print("\nAll leakage checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("FAIL  {0}".format(exc))
        sys.exit(1)
