"""
generate_dataset.py -- Create a small SYNTHETIC raw_sessions.csv in the ACN-Data schema.

Lets the whole pipeline (scripts 2-5) be tried without an API token or network.
It is NOT used for any reported result.

    python generate_dataset.py            # refuses to overwrite a real download
    python generate_dataset.py --force    # overwrite data/raw_sessions.csv anyway

5 stations x 35 days, seed 42: weekday arrival peak around 08:00, light weekends.
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from common import DATA_DIR

OUT = os.path.join(DATA_DIR, "raw_sessions.csv")
FIELDS = ["_id", "sessionID", "stationID", "spaceID", "siteID", "clusterID",
          "connectionTime", "disconnectTime", "doneChargingTime", "kWhDelivered",
          "timezone", "userID", "userInputs"]
RFC1123 = "%a, %d %b %Y %H:%M:%S GMT"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if os.path.exists(OUT) and not args.force:
        print("{0} already exists -- refusing to overwrite it. Use --force.".format(OUT))
        return 1

    rng = np.random.default_rng(42)
    stations = ["2-39-78-{0}".format(300 + i) for i in range(5)]
    start = datetime(2018, 5, 1, tzinfo=timezone.utc)
    rows = []

    for day in range(35):
        date = start + timedelta(days=day)
        weekend = date.weekday() >= 5
        for stn in stations:
            if rng.random() < (0.25 if weekend else 0.85):
                hour = float(np.clip(rng.normal(15.5, 1.8), 12, 22))   # 08:00 Pacific in UTC
                arrive = date + timedelta(hours=hour)
                depart = arrive + timedelta(hours=float(rng.uniform(2, 9)))
                kwh = round(float(rng.uniform(3, 30)), 3)
                sid = "{0}_{1}".format(stn.replace("-", "_"), arrive.isoformat())
                rows.append({
                    "_id": "synthetic{0:05d}".format(len(rows)), "sessionID": sid,
                    "stationID": stn, "spaceID": "SYN-{0}".format(stn[-3:]),
                    "siteID": "0002", "clusterID": "0039",
                    "connectionTime": arrive.strftime(RFC1123),
                    "disconnectTime": depart.strftime(RFC1123),
                    "doneChargingTime": depart.strftime(RFC1123),
                    "kWhDelivered": kwh, "timezone": "America/Los_Angeles",
                    "userID": "", "userInputs": "",
                })

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote {0:,} synthetic sessions to {1}".format(len(rows), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
