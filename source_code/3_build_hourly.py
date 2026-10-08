"""
3_build_hourly.py -- Turn the session table into station-wise and site-wide hourly demand.

Reads  data/sessions.csv
Writes data/hourly_station.csv   one row per (station, hour), zero-filled
       data/hourly_site.csv      one row per hour, summed over stations, zero-filled
       data/hourly.csv           hourly_site.csv plus the six model features
       data/stats.json           every dataset number the report quotes

Each session's energy is spread evenly over the hours of its CHARGING window
(connection -> charge_end_time), then summed. Cars often stay plugged in long
after they finish; spreading over the whole plugged-in window would invent demand
overnight and make the site series almost never zero.
A validation gate asserts that the reshaping neither loses nor creates energy.
"""

import json
import os
import sys

import numpy as np
import pandas as pd

from common import (DATA_DIR, SITE_TZ, TARGET, TOP_N_STATIONS, build_features,
                    read_hourly_csv)

SESSIONS_CSV = os.path.join(DATA_DIR, "sessions.csv")
STATION_CSV = os.path.join(DATA_DIR, "hourly_station.csv")
SITE_CSV = os.path.join(DATA_DIR, "hourly_site.csv")
HOURLY_CSV = os.path.join(DATA_DIR, "hourly.csv")
STATS_JSON = os.path.join(DATA_DIR, "stats.json")


def spread_sessions(sessions):
    """Return a (station_id, hour, demand_kwh) frame, one row per session-hour.

    Works in UTC so that daylight-saving changes cannot create ambiguous hours;
    the result is converted back to site-local time afterwards.
    """
    station, hour, kwh = [], [], []
    starts = sessions["connection_time"].dt.tz_convert("UTC")
    ends = sessions["charge_end_time"].dt.tz_convert("UTC")

    for stn, start, end, energy in zip(sessions["station_id"], starts, ends,
                                       sessions["kwh_delivered"]):
        span = pd.date_range(start.floor("h"), end.ceil("h"), freq="h")[:-1]
        if len(span) == 0:
            continue
        share = energy / len(span)
        station.extend([stn] * len(span))
        hour.extend(span)
        kwh.extend([share] * len(span))

    return pd.DataFrame({"station_id": station, "hour": hour, TARGET: kwh})


def main():
    if not os.path.exists(SESSIONS_CSV):
        print("Missing {0}. Run 2_build_sessions.py first.".format(SESSIONS_CSV))
        return 1

    sessions = pd.read_csv(SESSIONS_CSV)
    sessions["connection_time"] = pd.to_datetime(sessions["connection_time"], utc=True)
    sessions["charge_end_time"] = pd.to_datetime(sessions["charge_end_time"], utc=True)
    sessions["connection_time"] = sessions["connection_time"].dt.tz_convert(SITE_TZ)
    sessions["charge_end_time"] = sessions["charge_end_time"].dt.tz_convert(SITE_TZ)
    print("Sessions loaded: {0:,}".format(len(sessions)))

    spread = spread_sessions(sessions)
    print("Session-hours after spreading: {0:,}".format(len(spread)))

    # ------------------------------------------------------ station-wise hourly
    full_range = pd.date_range(spread["hour"].min(), spread["hour"].max(), freq="h")
    wide = (spread.groupby(["hour", "station_id"])[TARGET].sum()
                  .unstack("station_id")
                  .reindex(full_range, fill_value=0.0)
                  .fillna(0.0))
    wide.index.name = "hour"

    # ------------------------------------------------------ site-wide hourly
    site = wide.sum(axis=1).rename(TARGET).reset_index()

    # Validation gate: reshaping must neither lose nor create energy.
    energy_in = float(sessions["kwh_delivered"].sum())
    energy_out = float(site[TARGET].sum())
    station_out = float(wide.to_numpy().sum())
    print("Energy in sessions:      {0:>12,.2f} kWh".format(energy_in))
    print("Energy in hourly table:  {0:>12,.2f} kWh".format(energy_out))
    assert abs(energy_in - energy_out) < 1e-6, "site hourly total != session total"
    assert abs(energy_in - station_out) < 1e-6, "station hourly total != session total"
    print("Energy check passed (total kWh in = total kWh out).")

    # ------------------------------------------------------ write the tables
    to_local = lambda idx: idx.tz_convert(SITE_TZ)
    site["hour"] = to_local(pd.DatetimeIndex(site["hour"]))
    site.to_csv(SITE_CSV, index=False)

    long = (wide.stack().rename(TARGET).reset_index()
                .rename(columns={"level_0": "hour"}))
    long["hour"] = to_local(pd.DatetimeIndex(long["hour"]))
    long = long.sort_values(["station_id", "hour"])
    long.to_csv(STATION_CSV, index=False)

    features = build_features(read_hourly_csv(SITE_CSV))
    features.to_csv(HOURLY_CSV, index=False)

    # ------------------------------------------------------ statistics
    station_zero = (wide == 0).mean()                      # per-station zero share
    station_kwh = wide.sum().sort_values(ascending=False)
    top = station_kwh.head(TOP_N_STATIONS)
    stats = {
        "sessions": int(len(sessions)),
        "stations": int(wide.shape[1]),
        "hours": int(len(site)),
        "hours_with_features": int(len(features)),
        "first_hour": site["hour"].min().isoformat(),
        "last_hour": site["hour"].max().isoformat(),
        "total_kwh": round(energy_in, 2),
        "site_zero_share": round(float((site[TARGET] == 0).mean()), 4),
        "site_mean_kwh_per_h": round(float(site[TARGET].mean()), 2),
        "site_peak_kwh_per_h": round(float(site[TARGET].max()), 2),
        "station_zero_share_mean": round(float(station_zero.mean()), 4),
        "station_zero_share_median": round(float(station_zero.median()), 4),
        "station_zero_share_min": round(float(station_zero.min()), 4),
        "top_stations": [
            {"station_id": str(s), "total_kwh": round(float(k), 2),
             "zero_share": round(float(station_zero[s]), 4)}
            for s, k in top.items()
        ],
    }
    with open(STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    print("\nHourly rows (site):            {0:,}".format(stats["hours"]))
    print("Rows after lag features:       {0:,}".format(stats["hours_with_features"]))
    print("Stations:                      {0}".format(stats["stations"]))
    print("Site zero-demand hours:        {0:.1%}".format(stats["site_zero_share"]))
    print("Station zero-demand hours:     {0:.1%} (mean over stations)".format(
        stats["station_zero_share_mean"]))
    print("Mean site demand:              {0:.2f} kWh/h".format(
        stats["site_mean_kwh_per_h"]))
    print("Busiest stations: " + ", ".join(
        "{0} ({1:,.0f} kWh)".format(t["station_id"], t["total_kwh"])
        for t in stats["top_stations"]))
    print("Saved {0}, {1}, {2}, {3}".format(
        os.path.basename(STATION_CSV), os.path.basename(SITE_CSV),
        os.path.basename(HOURLY_CSV), os.path.basename(STATS_JSON)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
