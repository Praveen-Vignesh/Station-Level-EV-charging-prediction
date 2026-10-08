"""
2_build_sessions.py -- Clean the raw ACN-Data download into a tidy session table.

Reads  data/raw_sessions.csv
Writes data/sessions.csv   (station_id, connection_time, disconnect_time,
                            charge_end_time, kwh_delivered)
       data/cleaning_report.json

charge_end_time is when the vehicle stopped drawing energy: the API's
doneChargingTime, clamped so it never falls outside [connection, disconnect].
Cars often stay plugged in long after they finish, so the hourly builder spreads
each session's energy over the charging window, not the whole plugged-in window.

Times are converted from UTC to the site's local clock (America/Los_Angeles) so
that "weekday morning peak" and "hour of day" mean what they say.
"""

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
RAW_CSV = os.path.join(DATA_DIR, "raw_sessions.csv")
SESSIONS_CSV = os.path.join(DATA_DIR, "sessions.csv")
REPORT_JSON = os.path.join(DATA_DIR, "cleaning_report.json")

SITE_TZ = "America/Los_Angeles"
MAX_SESSION_HOURS = 72      # longer than this is a logging fault, not a charge


def main():
    if not os.path.exists(RAW_CSV):
        print("Missing {0}. Run 1_fetch_raw.py first.".format(RAW_CSV))
        return 1

    raw = pd.read_csv(RAW_CSV, dtype={"siteID": str, "clusterID": str})
    n_raw = len(raw)
    print("Raw rows read:            {0:>7,}".format(n_raw))

    df = pd.DataFrame({
        "session_id": raw["sessionID"],
        "station_id": raw["stationID"],
        "connection_time": pd.to_datetime(raw["connectionTime"], utc=True,
                                          errors="coerce", format="mixed"),
        "disconnect_time": pd.to_datetime(raw["disconnectTime"], utc=True,
                                          errors="coerce", format="mixed"),
        "charge_end_time": pd.to_datetime(raw["doneChargingTime"], utc=True,
                                          errors="coerce", format="mixed"),
        "kwh_delivered": pd.to_numeric(raw["kWhDelivered"], errors="coerce"),
    })

    dropped = {}

    def drop(mask, reason):
        """Remove rows where mask is True and remember how many went."""
        nonlocal df
        dropped[reason] = int(mask.sum())
        df = df[~mask]

    drop(df["session_id"].duplicated(keep="first"), "duplicate session_id")
    drop(df["station_id"].isna() | df["connection_time"].isna()
         | df["disconnect_time"].isna() | df["kwh_delivered"].isna(),
         "missing station / time / energy")
    drop(df["kwh_delivered"] <= 0, "energy <= 0 kWh")
    drop(df["disconnect_time"] <= df["connection_time"],
         "disconnect not after connection")
    hours = (df["disconnect_time"] - df["connection_time"]).dt.total_seconds() / 3600
    drop(hours > MAX_SESSION_HOURS, "longer than {0} h".format(MAX_SESSION_HOURS))

    # Charging window end: doneChargingTime, falling back to the disconnect time
    # when it is missing or not after the connection, and never past disconnect.
    unusable = df["charge_end_time"].isna() | (df["charge_end_time"] <= df["connection_time"])
    df["charge_end_time"] = df["charge_end_time"].mask(unusable, df["disconnect_time"])
    clamped = df["charge_end_time"] > df["disconnect_time"]
    df["charge_end_time"] = df["charge_end_time"].where(~clamped, df["disconnect_time"])
    dropped["(kept) doneChargingTime unusable, used disconnect"] = int(unusable.sum())
    dropped["(kept) doneChargingTime after disconnect, clamped"] = int(clamped.sum())

    for col in ("connection_time", "disconnect_time", "charge_end_time"):
        df[col] = df[col].dt.tz_convert(SITE_TZ)
    df = (df.sort_values("connection_time")
            .reset_index(drop=True)
            [["station_id", "connection_time", "disconnect_time",
              "charge_end_time", "kwh_delivered"]])

    df.to_csv(SESSIONS_CSV, index=False)

    report = {
        "rows_raw": n_raw,
        "dropped": dropped,
        "rows_clean": int(len(df)),
        "stations": int(df["station_id"].nunique()),
        "first_connection": df["connection_time"].min().isoformat(),
        "last_connection": df["connection_time"].max().isoformat(),
        "total_kwh": round(float(df["kwh_delivered"].sum()), 3),
        "timezone": SITE_TZ,
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    for reason, count in dropped.items():
        verb = "adjusted" if reason.startswith("(kept)") else "dropped "
        print("  {0} {1:>5,}  {2}".format(verb, count, reason.replace("(kept) ", "")))
    print("Clean sessions:           {0:>7,}".format(report["rows_clean"]))
    print("Stations:                 {0:>7,}".format(report["stations"]))
    print("First connection:         {0}".format(report["first_connection"]))
    print("Last connection:          {0}".format(report["last_connection"]))
    print("Total energy:             {0:>10,.2f} kWh".format(report["total_kwh"]))
    print("Saved {0}".format(SESSIONS_CSV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
