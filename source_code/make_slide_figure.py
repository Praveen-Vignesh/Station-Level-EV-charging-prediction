"""Regenerate the architecture diagram with higher contrast for the slide deck.

Reads the same real pipeline output as make_figures.py; writes figures/fig_4_1_slide.png.
Nothing is typed by hand -- every number comes from the JSON files.
"""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from common import DATA_DIR, FIG_DIR

# Higher-contrast palette (deeper fills, darker strokes) for a white slide background
BLUE, ORANGE, GREEN, PURPLE = "#1f6fd4", "#e05a1f", "#0b8f5d", "#4030a0"
OK_GREEN, GREY, INK = "#0a8f0a", "#6f6f68", "#0b0b0b"
PANEL, PANEL_EDGE = "#eef0f4", "#b9b9ad"
ARROW = "#7d7d70"
DPI = 220


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def canvas(w, h):
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, text, edge, face="white", size=5.9, weight="bold", color=INK):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.04",
                                lw=1.7, edgecolor=edge, facecolor=face))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size,
            fontweight="normal", color=color, linespacing=1.35)


def arrow(ax, x0, y0, x1, y1):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", color=ARROW, lw=2.0, shrinkA=0, shrinkB=0))


def main():
    st = load("stats.json")
    clean = load("cleaning_report.json")
    W, H = 6.05, 4.01
    fig, ax = canvas(W, H)
    layers = [
        ("EXTERNAL\nSOURCE", BLUE, "#d3e3fb"),
        ("INGESTION\nLAYER", ORANGE, "#fad7c4"),
        ("TRANSFORMATION\nLAYER", GREEN, "#c3ebd5"),
        ("MODELLING\nLAYER", PURPLE, "#d6cef2"),
        ("PRESENTATION\nLAYER", BLUE, "#d3e3fb"),
    ]
    top, gap = 0.06, 0.07
    lh = (H - top - gap * 4 - 0.05) / 5
    ys = [top + i * (lh + gap) for i in range(5)]
    for (label, edge, face), y in zip(layers, ys):
        ax.add_patch(FancyBboxPatch((0.04, y), W - 0.08, lh, boxstyle="round,pad=0,rounding_size=0.05",
                                    lw=1.3, edgecolor=PANEL_EDGE, facecolor=PANEL))
        ax.add_patch(plt.Rectangle((0.04, y), 0.06, lh, color=edge))
        ax.text(0.19, y + lh / 2, label, fontsize=5.8, fontweight="bold", color=edge,
                va="center", linespacing=1.3)
    for i in range(4):
        arrow(ax, W / 2, ys[i] + lh, W / 2, ys[i + 1])

    bx = 1.30
    full = W - 0.16 - bx

    def row(i, items, edges, faces):
        n = len(items)
        g = 0.14
        w = (full - g * (n - 1)) / n
        for k, text in enumerate(items):
            x = bx + k * (w + g)
            box(ax, x, ys[i] + 0.07, w, lh - 0.14, text,
                edges[k] if isinstance(edges, list) else edges,
                faces[k])
            if k < n - 1:
                arrow(ax, x + w, ys[i] + lh / 2, x + w + g, ys[i] + lh / 2)

    row(0, ["Caltech ACN-Data REST API\nGET /api/v1/sessions/caltech\ntoken auth  ·  100 rows per page"],
        BLUE, ["#d3e3fb"])
    row(1, ["1_fetch_raw.py\nretry x5 · append + fsync\nper page · resumable",
            "raw_sessions.csv\n{0:,} rows".format(clean["rows_raw"]),
            "2_build_sessions.py\nclean · UTC → Pacific\n→ sessions.csv"],
        [ORANGE, GREY, GREY], ["#fad7c4", "#e6e6e1", "#e6e6e1"])
    row(2, ["3_build_hourly.py\nspread kWh over charging window\ngroupby hour · zero-fill",
            "Validation gate\nassert kWh in = kWh out\n{0:,.2f} kWh ✓".format(st["total_kwh"]),
            "hourly_station.csv\nhourly_site.csv\nhourly.csv ({0:,} rows)".format(st["hours_with_features"])],
        [GREEN, OK_GREEN, GREY], ["#c3ebd5", "#c3ebd5", "#e6e6e1"])
    row(3, ["Feature builder\nhour · weekday · lags\n1 / 2 / 24 / 168 h",
            "Seasonal-naive\nbaseline (t − 168 h)",
            "4_train_model.py\nLinearRegression\nRandomForest (CV-tuned)"],
        [PURPLE, GREY, PURPLE], ["#d6cef2", "#e6e6e1", "#d6cef2"])
    row(4, ["MAE + RMSE vs baseline\ntest_no_leakage.py",
            "metrics.json · figures\npredictions.csv",
            "5_dashboard_server.py\nGET /api/data → JSON\nlocalhost:8000"],
        BLUE, ["#d3e3fb"] * 3)

    os.makedirs(FIG_DIR, exist_ok=True)
    path = os.path.join(FIG_DIR, "fig_4_1_slide.png")
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    main()
