"""
1_fetch_raw.py -- Download charging sessions from the Caltech ACN-Data REST API.

The API is paged and occasionally times out, so this script is built to be
interrupted and restarted without losing anything:

  * every page is appended to data/raw_sessions.csv and flushed to disk
    immediately, so a crash costs at most the page in flight;
  * progress is recorded in data/download_state.json, so re-running the script
    resumes from the next unfetched page;
  * each page is retried up to 5 times with a few minutes between attempts.

Usage:
    python 1_fetch_raw.py                 # fetch up to TARGET_ROWS, resuming
    python 1_fetch_raw.py --rows 3000     # different target
    python 1_fetch_raw.py --restart       # discard progress and start over
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------- configuration

BASE_URL = "https://ev.caltech.edu/api/v1/"
SITE = "caltech"

# A free token is obtained by registering at https://ev.caltech.edu/dataset.
# The public DEMO_TOKEN works for the caltech site and is used by default.
TOKEN = os.environ.get("ACN_API_TOKEN", "DEMO_TOKEN")

TARGET_ROWS = 6500          # size of the extract used in the report
PAGE_SIZE = 100             # rows per API request
REQUEST_TIMEOUT = 60        # seconds before a single request is abandoned

# Waits between retry attempts, in seconds. Five attempts, a few minutes apart,
# which is what it took to get through the API's timeouts in practice.
RETRY_WAITS = [60, 120, 180, 300, 300]
MAX_ATTEMPTS = len(RETRY_WAITS)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RAW_CSV = os.path.join(DATA_DIR, "raw_sessions.csv")
STATE_FILE = os.path.join(DATA_DIR, "download_state.json")

# userInputs is a nested object, so it is stored as a JSON string to keep
# raw_sessions.csv a faithful copy of what the API returned.
FIELDNAMES = [
    "_id", "sessionID", "stationID", "spaceID", "siteID", "clusterID",
    "connectionTime", "disconnectTime", "doneChargingTime", "kWhDelivered",
    "timezone", "userID", "userInputs",
]

# ---------------------------------------------------------------- state on disk


def load_state():
    """Return {'next_page', 'rows_written'}, defaulting to a fresh start."""
    if not (os.path.exists(STATE_FILE) and os.path.exists(RAW_CSV)):
        return {"next_page": 1, "rows_written": 0}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        return {
            "next_page": int(state.get("next_page", 1)),
            "rows_written": int(state.get("rows_written", 0)),
        }
    except (ValueError, OSError):
        print("! download_state.json is unreadable -- starting from page 1")
        return {"next_page": 1, "rows_written": 0}


def save_state(next_page, rows_written):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "next_page": next_page,
                "rows_written": rows_written,
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def clear_progress():
    for path in (RAW_CSV, STATE_FILE):
        if os.path.exists(path):
            os.remove(path)
    print("Cleared previous download progress.")


# ---------------------------------------------------------------- the API call


def fetch_page(page, page_size):
    """Fetch one page of sessions, retrying on timeout or HTTP error.

    Returns the decoded JSON payload. Raises RuntimeError if every attempt
    fails, leaving whatever is already on disk untouched.
    """
    url = "{0}sessions/{1}?max_results={2}&page={3}".format(
        BASE_URL, SITE, page_size, page
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, auth=(TOKEN, ""), timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            reason = "HTTP {0}".format(response.status_code)
        except requests.exceptions.RequestException as exc:
            reason = type(exc).__name__
        except ValueError:
            reason = "malformed JSON"

        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                "page {0} failed after {1} attempts (last error: {2})".format(
                    page, MAX_ATTEMPTS, reason
                )
            )

        wait = RETRY_WAITS[attempt - 1]
        print(
            "  attempt {0}/{1} failed ({2}) -- waiting {3}s before retry".format(
                attempt, MAX_ATTEMPTS, reason, wait
            ),
            flush=True,
        )
        time.sleep(wait)


# ---------------------------------------------------------------- writing rows


def open_csv():
    """Open raw_sessions.csv for appending, writing the header if it is new."""
    is_new = not os.path.exists(RAW_CSV) or os.path.getsize(RAW_CSV) == 0
    handle = open(RAW_CSV, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
    if is_new:
        writer.writeheader()
    return handle, writer


def write_rows(handle, writer, sessions):
    """Append sessions and force them to disk before returning."""
    for session in sessions:
        row = {key: session.get(key) for key in FIELDNAMES}
        if isinstance(row["userInputs"], (dict, list)):
            row["userInputs"] = json.dumps(row["userInputs"])
        writer.writerow(row)
    handle.flush()
    os.fsync(handle.fileno())


# ---------------------------------------------------------------------- driver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=TARGET_ROWS,
                        help="number of sessions to download (default %(default)s)")
    parser.add_argument("--restart", action="store_true",
                        help="discard existing progress and download from scratch")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    if args.restart:
        clear_progress()

    state = load_state()
    page = state["next_page"]
    rows_written = state["rows_written"]
    target = args.rows

    if rows_written >= target:
        print("Already have {0:,} rows (target {1:,}). Nothing to do.".format(
            rows_written, target))
        print("Use --restart to download again, or --rows N for a larger target.")
        return 0

    if rows_written:
        print("Resuming: {0:,} rows already saved, continuing from page {1}.".format(
            rows_written, page))

    total_pages = -(-target // PAGE_SIZE)  # ceiling division
    print("Downloading up to {0:,} sessions from the {1} site ({2} rows/page).".format(
        target, SITE, PAGE_SIZE))
    print("Token: {0}".format("ACN_API_TOKEN from environment"
                              if TOKEN != "DEMO_TOKEN" else "DEMO_TOKEN (public)"))
    print("Writing to {0}\n".format(RAW_CSV))

    handle, writer = open_csv()
    try:
        while rows_written < target:
            payload = fetch_page(page, PAGE_SIZE)
            sessions = payload.get("_items", [])
            if not sessions:
                print("Page {0} returned no rows -- stopping.".format(page))
                break

            # Do not overshoot the target on the final page.
            remaining = target - rows_written
            if len(sessions) > remaining:
                sessions = sessions[:remaining]

            write_rows(handle, writer, sessions)
            rows_written += len(sessions)
            page += 1
            save_state(page, rows_written)

            print("page {0}/{1} | {2:,} rows | ok".format(
                page - 1, total_pages, rows_written), flush=True)

            if "next" not in payload.get("_links", {}):
                print("Reached the last page available from the API.")
                break
    except KeyboardInterrupt:
        print("\nInterrupted. {0:,} rows are saved; re-run to resume.".format(
            rows_written))
        return 130
    except RuntimeError as exc:
        print("\nDownload stopped: {0}".format(exc))
        print("{0:,} rows are saved in {1}. Re-run to resume from page {2}.".format(
            rows_written, RAW_CSV, page))
        return 1
    finally:
        handle.close()

    print("\nDone. {0:,} sessions saved to {1}".format(rows_written, RAW_CSV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
