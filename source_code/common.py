"""
common.py -- constants and helpers shared by the pipeline scripts.

Script names start with a digit (1_fetch_raw.py ...) so they cannot be imported;
anything that more than one script needs lives here instead. In particular
build_features() is the single definition of the model inputs, used by the
hourly builder, the trainer and the leakage test.
"""

import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
MODEL_DIR = os.path.join(HERE, "models")
FIG_DIR = os.path.join(HERE, "figures")
WEB_DIR = os.path.join(HERE, "web")

SITE_TZ = "America/Los_Angeles"

LAGS = (1, 2, 24, 168)
FEATURES = ["hour_of_day", "weekday", "lag_1h", "lag_2h", "lag_24h", "lag_168h"]
TARGET = "demand_kwh"

TEST_FRACTION = 0.2
RANDOM_STATE = 42
TOP_N_STATIONS = 5


def read_hourly_csv(path):
    """Read a CSV whose 'hour' column holds tz-aware ISO timestamps (site local)."""
    df = pd.read_csv(path)
    df["hour"] = pd.to_datetime(df["hour"], utc=True).dt.tz_convert(SITE_TZ)
    return df


def build_features(hourly):
    """Add calendar and strictly backward-looking lag features.

    `hourly` has one row per consecutive hour with columns 'hour' (tz-aware,
    site local) and 'demand_kwh'. The rows must be a gap-free hourly series,
    because a lag is a row shift: shift(168) is only "one week earlier" if every
    hour is present.

    Every lag uses shift(k) with k >= 1, so the value in row i is demand from
    hour i-k -- nothing at or after hour i can reach the features. The first
    168 rows have no full history and are dropped.
    """
    out = hourly.sort_values("hour").reset_index(drop=True).copy()
    out["hour_of_day"] = out["hour"].dt.hour
    out["weekday"] = out["hour"].dt.weekday
    for lag in LAGS:
        out["lag_{0}h".format(lag)] = out[TARGET].shift(lag)
    return out.dropna().reset_index(drop=True)


def chronological_split(df, test_fraction=TEST_FRACTION):
    """Earliest rows train, latest rows test. Never shuffled."""
    df = df.sort_values("hour").reset_index(drop=True)
    split = int(len(df) * (1 - test_fraction))
    return df.iloc[:split], df.iloc[split:]
