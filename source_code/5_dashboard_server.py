"""
5_dashboard_server.py -- Local web dashboard for the forecasting results.

Run:   python 5_dashboard_server.py          then open http://localhost:8000
       python 5_dashboard_server.py --port 8080

Serves the static page in web/ and one JSON endpoint:
    GET /api/data   metrics.json + stats.json + predictions.csv + station_results.csv

Python standard library only. It listens on 127.0.0.1, so it is reachable from
this machine only. Data files are re-read on every request, so re-running
4_train_model.py updates the page on refresh -- no restart needed.
"""

import argparse
import csv
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import DATA_DIR, WEB_DIR

HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# Only these files can be served from web/ -- nothing else is reachable.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/chart.umd.min.js": ("chart.umd.min.js", "application/javascript; charset=utf-8"),
}


def read_json(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def read_csv_rows(name):
    """Read a CSV into a list of dicts, converting numeric-looking fields."""
    with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key, value in row.items():
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass
    return rows


def build_payload():
    """Assemble the /api/data response from the pipeline's output files."""
    predictions = read_csv_rows("predictions.csv")
    return {
        "metrics": read_json("metrics.json"),
        "stats": read_json("stats.json"),
        "stations": read_csv_rows("station_results.csv"),
        # Column-oriented keeps the JSON small for the ~600-point charts.
        "predictions": {
            col: [row[col] for row in predictions]
            for col in ("hour", "actual", "baseline", "linear", "rf_fixed", "rf")
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "EVDashboard/1.0"

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/data":
            try:
                body = json.dumps(build_payload()).encode("utf-8")
            except FileNotFoundError as exc:
                msg = {"error": "Pipeline output missing: {0}. Run scripts 1-4 first."
                       .format(os.path.basename(exc.filename or "?"))}
                self._send(503, json.dumps(msg).encode("utf-8"), "application/json")
                return
            self._send(200, body, "application/json")
            return

        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            try:
                with open(os.path.join(WEB_DIR, filename), "rb") as f:
                    self._send(200, f.read(), content_type)
            except FileNotFoundError:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("  {0}\n".format(fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    try:
        server = ThreadingHTTPServer((HOST, args.port), Handler)
    except OSError as exc:
        print("Could not bind {0}:{1} -- {2}".format(HOST, args.port, exc))
        print("Try another port:  python 5_dashboard_server.py --port 8080")
        return 1

    print("EV charging demand dashboard")
    print("  open  http://localhost:{0}".format(args.port))
    print("  JSON  http://localhost:{0}/api/data".format(args.port))
    print("  Ctrl-C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
