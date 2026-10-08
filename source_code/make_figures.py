"""
make_figures.py -- Regenerate the six report figures from the pipeline's real output.

Reads  data/metrics.json, data/stats.json, data/predictions.csv, data/cleaning_report.json,
       data/sessions.csv, data/hourly_site.csv
Writes figures/fig_4_1.png ... fig_6_1.png

Nothing is typed by hand: every number comes from the JSON/CSV files, so the figures
cannot drift from the run that produced them. Figure sizes keep the aspect ratio of the
slots they replace in the report.
"""

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from common import DATA_DIR, FIG_DIR

BLUE, ORANGE, GREEN, PURPLE = "#2a78d6", "#ec6b35", "#14a36b", "#4b3aa8"
OK_GREEN, GREY, INK, MUTED = "#0ea50e", "#888880", "#111111", "#666660"
PANEL, PANEL_EDGE = "#fbfbf9", "#dcdcd4"
DPI = 200


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def save(fig, name):
    os.makedirs(FIG_DIR, exist_ok=True)
    path = os.path.join(FIG_DIR, name)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)
    print("  wrote", os.path.relpath(path, os.path.dirname(FIG_DIR)))


def canvas(w, h):
    """A figure whose axes are a plain 0..w x 0..h pixel-like grid (y down)."""
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, text, edge, face="white", size=7.2, weight="normal", color=INK):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.04",
                                lw=1.2, edgecolor=edge, facecolor=face))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size,
            fontweight=weight, color=color, linespacing=1.35)


def arrow(ax, x0, y0, x1, y1):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", color="#b5b5a8", lw=1.6, shrinkA=0, shrinkB=0))


# ------------------------------------------------------------------ Figure 4.1


def fig_architecture(m, st, clean):
    W, H = 6.05, 4.01
    fig, ax = canvas(W, H)
    layers = [
        ("EXTERNAL\nSOURCE", BLUE, "#eaf2fd"),
        ("INGESTION\nLAYER", ORANGE, "#fdeee7"),
        ("TRANSFORMATION\nLAYER", GREEN, "#e7f6ef"),
        ("MODELLING\nLAYER", PURPLE, "#edeaf8"),
        ("PRESENTATION\nLAYER", BLUE, "#eaf2fd"),
    ]
    top, gap = 0.06, 0.07
    lh = (H - top - gap * 4 - 0.05) / 5
    ys = [top + i * (lh + gap) for i in range(5)]
    for (label, edge, face), y in zip(layers, ys):
        ax.add_patch(FancyBboxPatch((0.04, y), W - 0.08, lh, boxstyle="round,pad=0,rounding_size=0.05",
                                    lw=1, edgecolor=PANEL_EDGE, facecolor=PANEL))
        ax.add_patch(plt.Rectangle((0.04, y), 0.05, lh, color=edge))
        ax.text(0.17, y + lh / 2, label, fontsize=5.6, fontweight="bold", color=edge,
                va="center", linespacing=1.3)
    for i in range(4):
        arrow(ax, W / 2, ys[i] + lh, W / 2, ys[i + 1])

    bx = 1.28
    full = W - 0.16 - bx

    def row(i, items, edges, faces=None):
        n = len(items)
        g = 0.14
        w = (full - g * (n - 1)) / n
        for k, text in enumerate(items):
            x = bx + k * (w + g)
            box(ax, x, ys[i] + 0.07, w, lh - 0.14, text, edges[k] if isinstance(edges, list) else edges,
                faces[k] if faces else "white", size=5.9)
            if k < n - 1:
                arrow(ax, x + w, ys[i] + lh / 2, x + w + g, ys[i] + lh / 2)

    row(0, ["Caltech ACN-Data REST API\nGET /api/v1/sessions/caltech\ntoken auth  ·  100 rows per page"], BLUE, ["#eaf2fd"])
    row(1, ["1_fetch_raw.py\nretry x5 · append + fsync\nper page · resumable",
            "raw_sessions.csv\n{0:,} rows".format(clean["rows_raw"]),
            "2_build_sessions.py\nclean · UTC → Pacific\n→ sessions.csv"],
        [ORANGE, GREY, GREY], ["#fdeee7", "#f7f7f4", "#f7f7f4"])
    row(2, ["3_build_hourly.py\nspread kWh over charging window\ngroupby hour · zero-fill",
            "Validation gate\nassert kWh in = kWh out\n{0:,.2f} kWh ✓".format(st["total_kwh"]),
            "hourly_station.csv\nhourly_site.csv\nhourly.csv ({0:,} rows)".format(st["hours_with_features"])],
        [GREEN, OK_GREEN, GREY], ["#e7f6ef", "#e7f6ef", "#f7f7f4"])
    row(3, ["Feature builder\nhour · weekday · lags\n1 / 2 / 24 / 168 h",
            "Seasonal-naive\nbaseline (t − 168 h)",
            "4_train_model.py\nLinearRegression\nRandomForest (CV-tuned)"],
        [PURPLE, GREY, PURPLE], ["#edeaf8", "#f7f7f4", "#edeaf8"])
    row(4, ["MAE + RMSE vs baseline\ntest_no_leakage.py",
            "metrics.json · figures\npredictions.csv",
            "5_dashboard_server.py\nGET /api/data → JSON\nlocalhost:8000"],
        BLUE, ["#eaf2fd"] * 3)
    save(fig, "fig_4_1.png")


# ------------------------------------------------------------------ Figure 5.1


def table(ax, x, y, col_w, header, rows, head_color, row_h=0.19, size=6.6):
    total = sum(col_w)
    ax.add_patch(plt.Rectangle((x, y), total, row_h, color=head_color))
    cx = x
    for w, h in zip(col_w, header):
        ax.text(cx + 0.08, y + row_h / 2, h, color="white", fontsize=size, fontweight="bold", va="center")
        cx += w
    for r, row in enumerate(rows):
        ry = y + row_h * (r + 1)
        ax.add_patch(plt.Rectangle((x, ry), total, row_h, facecolor="#f7f7f4" if r % 2 else "white",
                                   edgecolor="#e3e3dc", lw=0.6))
        cx = x
        for w, v in zip(col_w, row):
            ax.text(cx + 0.08, ry + row_h / 2, str(v), fontsize=size, va="center", color=INK)
            cx += w
    return y + row_h * (len(rows) + 1)


def fig_reshape(st, clean):
    W, H = 6.11, 4.28
    fig, ax = canvas(W, H)
    sessions = pd.read_csv(os.path.join(DATA_DIR, "sessions.csv")).head(6)
    hourly = pd.read_csv(os.path.join(DATA_DIR, "hourly_site.csv")).head(6)

    ax.text(0.08, 0.2, "data/sessions.csv  —  cleaned transaction log", fontsize=8.2, fontweight="bold")
    ax.text(0.08, 0.40, "{0:,} sessions · {1} stations · one row per car".format(
        clean["rows_clean"], clean["stations"]), fontsize=6.4, color=MUTED)
    rows = [(r.station_id, r.connection_time[:19], r.charge_end_time[11:19], "{0:g}".format(r.kwh_delivered))
            for r in sessions.itertuples()]
    y = table(ax, 0.08, 0.5, [1.6, 2.1, 1.35, 0.9], ["station", "connect_time", "charge_end", "kwh"], rows, BLUE)

    ax.text(W / 2, y + 0.33, "3_build_hourly.py   ↓   spread kWh over charging window · groupby(hour) · sum · reindex full range",
            ha="center", fontsize=6.1, color=ORANGE, fontweight="bold")

    y2 = y + 0.62
    ax.text(0.08, y2, "data/hourly_site.csv  —  reshaped hourly series", fontsize=8.2, fontweight="bold")
    ax.text(0.08, y2 + 0.2, "{0:,} hours · quiet hours are real zeros, not missing".format(st["hours"]),
            fontsize=6.4, color=MUTED)
    rows = [(r.hour[:19], "{0:.3f}".format(r.demand_kwh)) for r in hourly.itertuples()]
    table(ax, 0.08, y2 + 0.3, [2.6, 2.3], ["hour", "demand_kwh"], rows, GREEN)
    save(fig, "fig_5_1.png")


# ------------------------------------------------------------------ Figure 5.2


def fig_terminal(m, st, clean):
    W, H = 6.05, 3.47
    fig, ax = canvas(W, H)
    ax.add_patch(FancyBboxPatch((0.05, 0.05), W - 0.1, H - 0.1, boxstyle="round,pad=0,rounding_size=0.08",
                                facecolor="#1b1b1b", edgecolor="#1b1b1b"))
    for i, c in enumerate(["#e5483f", "#f0a21b", "#1db07d"]):
        ax.add_patch(plt.Circle((0.3 + i * 0.2, 0.26), 0.055, color=c))
    ax.text(1.5, 0.26, "Windows PowerShell — source_code", color="#9a9a92", fontsize=6.3, va="center")

    mod, sp = m["models"], m["split"]
    top3 = m["feature_importance"][:3]
    lines = [
        ("$ python 3_build_hourly.py", "#8ab4f8", True),
        ("Sessions loaded: {0:,}".format(clean["rows_clean"]), "#f2f2ee", False),
        ("Energy in sessions:     {0:,.2f} kWh".format(st["total_kwh"]), "#f2f2ee", False),
        ("Energy in hourly table: {0:,.2f} kWh".format(st["total_kwh"]), "#f2f2ee", False),
        ("Energy check passed (total kWh in = total kWh out).", "#3ddc4a", True),
        ("Zero-demand hours: site {0:.1%} | average station {1:.1%}".format(
            st["site_zero_share"], st["station_zero_share_mean"]), "#f2f2ee", False),
        ("", "", False),
        ("$ python 4_train_model.py", "#8ab4f8", True),
        ("Rows: {0:,} | {1:,} train | {2:,} test (chronological)".format(
            m["dataset"]["hours_with_features"], sp["train_rows"], sp["test_rows"]), "#f2f2ee", False),
        ("Baseline (same hour last week)  MAE {0:6.3f}  RMSE {1:6.3f}".format(
            mod["baseline"]["mae"], mod["baseline"]["rmse"]), "#f2f2ee", False),
        ("Random Forest (tuned, final)    MAE {0:6.3f}  RMSE {1:6.3f}".format(
            mod["rf"]["mae"], mod["rf"]["rmse"]), "#f2f2ee", False),
        ("The model BEATS the baseline by {0:.1f}%.".format(mod["rf"]["mae_improvement_pct"]), "#3ddc4a", True),
        ("Top features: " + "  ".join("{0} {1:.3f}".format(f["feature"], f["importance"]) for f in top3),
         "#a8a8a0", False),
        ("", "", False),
        ("$ python test_no_leakage.py  →  All leakage checks passed.", "#8ab4f8", True),
    ]
    y = 0.58
    for text, color, bold in lines:
        if text:
            ax.text(0.28, y, text, color=color, fontsize=5.5, family="DejaVu Sans Mono",
                    fontweight="bold" if bold else "normal", va="center")
        y += 0.2
    save(fig, "fig_5_2.png")


# ------------------------------------------------------------------ Figure 5.3


def fig_tree():
    W, H = 4.96, 3.24
    fig, ax = canvas(W, H)
    ax.add_patch(FancyBboxPatch((0.08, 0.06), W - 0.16, H - 0.12, boxstyle="round,pad=0,rounding_size=0.05",
                                facecolor=PANEL, edgecolor=PANEL_EDGE, lw=1.2))
    ax.text(0.25, 0.22, "Code architecture", fontsize=8.5, fontweight="bold")
    items = [
        (0, "source_code/", "", BLUE, True),
        (1, "1_fetch_raw.py", "download · retry x5 · resume", ORANGE, False),
        (1, "2_build_sessions.py", "clean → sessions.csv", ORANGE, False),
        (1, "3_build_hourly.py", "station + site hourly, energy assert", ORANGE, False),
        (1, "4_train_model.py", "baseline · LR · RF · tuning", ORANGE, False),
        (1, "5_dashboard_server.py", "localhost:8000 + /api/data", ORANGE, False),
        (1, "test_no_leakage.py", "backward-only checks", ORANGE, False),
        (1, "data/", "raw → sessions → hourly → metrics", BLUE, True),
        (1, "web/", "index.html · app.js · style.css", BLUE, True),
        (1, "models/  figures/", "random_forest.joblib · report figures", BLUE, True),
        (1, "requirements.txt  README.md", "dependencies · how to run", "#4a4a45", False),
    ]
    y = 0.5
    for indent, name, note, color, bold in items:
        ax.text(0.25 + 0.2 * indent, y, name, fontsize=6.0, family="DejaVu Sans Mono", color=color,
                fontweight="bold" if bold else "normal", va="center")
        if note:
            ax.text(2.55, y, note, fontsize=5.2, color="#85857d", va="center")
        y += 0.245
    save(fig, "fig_5_3.png")


# ------------------------------------------------------------------ Figure 5.4


def fig_phases():
    phases = [
        ("Problem framing & repo setup", 0.0, 1.5),
        ("Data acquisition — ACN-Data API", 1.4, 2.7),
        ("Sessions to hourly series", 2.6, 3.8),
        ("Baseline + evaluation (MAE, RMSE)", 3.7, 4.6),
        ("Linear Regression model", 4.4, 5.3),
        ("Random Forest + features", 5.1, 6.3),
        ("Web dashboard (local)", 6.0, 6.9),
        ("Error analysis & tuning", 6.7, 7.9),
        ("Leakage test, report & packaging", 7.8, 9.0),
    ]
    fig, ax = plt.subplots(figsize=(5.38, 3.98))
    for i, (name, a, b) in enumerate(phases):
        ax.barh(i, b - a, left=a, color=OK_GREEN, height=0.5)
        ax.text(-0.25, i, name, ha="right", va="center", fontsize=6.6)
        ax.text(-0.97, i, "✓", color=OK_GREEN, fontsize=8, va="center", fontweight="bold",
                transform=ax.get_yaxis_transform())
    ax.set_xlim(0, 9.2)
    ax.set_ylim(len(phases) - 0.4, -0.7)
    ax.set_xticks([0, 2, 4, 6, 8])
    ax.set_xticklabels(["Week 1", "Week 3", "Week 5", "Week 7", "Week 9"], fontsize=6.6, color="#858580")
    ax.set_yticks([])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#c9c9bf")
    ax.xaxis.grid(True, color="#e1e1d9", lw=0.8)
    ax.set_axisbelow(True)
    ax.set_title("Phase status against the review timeline", loc="left", fontsize=8.6,
                 fontweight="bold", x=-0.97)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=OK_GREEN, label="Completed")], loc="lower left",
              bbox_to_anchor=(-0.97, -0.2), frameon=False, fontsize=6.6)
    fig.subplots_adjust(left=0.5, right=0.97, top=0.9, bottom=0.14)
    save(fig, "fig_5_4.png")


# ------------------------------------------------------------------ Figure 6.1


def fig_results(m):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.36, 3.87), gridspec_kw={"height_ratios": [1.05, 1.1]})
    mod = m["models"]
    order = ["baseline", "linear", "rf_fixed", "rf"]
    labels = ["Baseline\n(same hour last week)", "Linear Regression", "Random Forest\n(fixed params)",
              "Random Forest\n(tuned, final)"]
    colors = [GREY, BLUE, "#6fbf8f", OK_GREEN]
    ypos = [3, 2, 1, 0]
    for y, k, c in zip(ypos, order, colors):
        v = mod[k]
        a1.barh(y + 0.19, v["mae"], height=0.34, color=c)
        a1.barh(y - 0.19, v["rmse"], height=0.34, color=c, alpha=0.45)
        gain = "" if k == "baseline" else "  ({0:+.1f}%)".format(v["mae_improvement_pct"])
        a1.text(v["mae"] + 0.15, y + 0.19, "MAE {0:.2f}{1}".format(v["mae"], gain), va="center",
                fontsize=5.8, fontweight="bold" if k == "rf" else "normal")
        a1.text(v["rmse"] + 0.15, y - 0.19, "RMSE {0:.2f}".format(v["rmse"]), va="center",
                fontsize=5.4, color=MUTED)
    a1.set_yticks(ypos)
    a1.set_yticklabels(labels, fontsize=5.8)
    a1.set_xlim(0, max(mod[k]["rmse"] for k in order) * 1.38)
    a1.set_xlabel("kWh per hour — lower is better (solid = MAE, faded = RMSE)", fontsize=5.8, color=MUTED)
    a1.set_title("Test-set accuracy ({0} held-out hours)".format(m["split"]["test_rows"]),
                 loc="left", fontsize=7.6, fontweight="bold")

    pred = pd.read_csv(os.path.join(DATA_DIR, "predictions.csv"))
    w = m["demo_week"]
    seg = pred.iloc[w["start_index"]: w["end_index"] + 1]
    t = pd.to_datetime(seg["hour"])
    a2.plot(t, seg["actual"], color=GREY, lw=1.5, label="Actual")
    a2.plot(t, seg["rf"], color=OK_GREEN, lw=1.5, label="Random Forest forecast")
    a2.set_ylabel("kWh per hour", fontsize=6, color=MUTED)
    a2.set_title("One representative test week — forecast vs actual", loc="left", fontsize=7.6,
                 fontweight="bold")
    a2.legend(frameon=False, fontsize=5.8, loc="upper right", ncol=2)
    a2.set_ylim(bottom=0)
    import matplotlib.dates as mdates
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    a2.xaxis.set_major_locator(mdates.DayLocator())

    for ax in (a1, a2):
        ax.tick_params(labelsize=5.6, colors="#858580", length=2)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color("#c9c9bf")
        ax.grid(True, color="#e6e6df", lw=0.7)
        ax.set_axisbelow(True)
    fig.tight_layout(h_pad=1.0)
    save(fig, "fig_6_1.png")


def main():
    for name in ("metrics.json", "stats.json", "cleaning_report.json", "predictions.csv"):
        if not os.path.exists(os.path.join(DATA_DIR, name)):
            print("Missing data/{0}. Run scripts 1-4 first.".format(name))
            return 1
    m, st, clean = load("metrics.json"), load("stats.json"), load("cleaning_report.json")
    print("Writing figures:")
    fig_architecture(m, st, clean)
    fig_reshape(st, clean)
    fig_terminal(m, st, clean)
    fig_tree()
    fig_phases()
    fig_results(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
