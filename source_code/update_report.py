"""
update_report.py -- Rewrite the PBL report .docx from the pipeline's real output.

Reads  data/metrics.json, data/stats.json, data/cleaning_report.json,
       data/station_results.csv, data/hourly_site.csv, figures/fig_*.png
Writes ../PBL_Report_EV_Charging_Demand_Forecasting.docx

The first run copies the original report to *_backup_before_update.docx. Every run
starts from that backup, so the script can be re-run safely after retraining.
Every figure and number in the new text is formatted from the data files; nothing
is typed by hand. Page numbers in the contents tables are refreshed separately by
refresh_page_numbers.py once the document has been rendered by Word.
"""

import copy
import csv
import json
import os
import shutil
import sys
from datetime import datetime

import docx
import pandas as pd
from docx.oxml.ns import qn
from docx.table import Table
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FIG = os.path.join(HERE, "figures")
ROOT = os.path.dirname(HERE)
REPORT = os.path.join(ROOT, "PBL_Report_EV_Charging_Demand_Forecasting.docx")
BACKUP = os.path.join(ROOT, "PBL_Report_EV_Charging_Demand_Forecasting_backup_before_update.docx")


# ----------------------------------------------------------------------- data


def jload(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


M, ST, CL = jload("metrics.json"), jload("stats.json"), jload("cleaning_report.json")
with open(os.path.join(DATA, "station_results.csv"), newline="", encoding="utf-8") as f:
    STATIONS = list(csv.DictReader(f))
SITE = pd.read_csv(os.path.join(DATA, "hourly_site.csv"))

MOD = M["models"]
SP = M["split"]
ABL = M["ablation"]
BEST = M["tuning"]["best_params"]
IMP = {d["feature"]: d["importance"] for d in M["feature_importance"]}


def f2(x):
    return "{0:.2f}".format(float(x))


def pct(x):
    return "{0:.1f}%".format(float(x))


def dmy(stamp):
    """'2018-07-20 17:00' -> '20 July 2018'."""
    return datetime.strptime(stamp[:10], "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")


def hours_phrase(hours):
    labels = ["{0:02d}:00".format(h) for h in sorted(hours)]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


N_RAW, N = CL["rows_raw"], CL["rows_clean"]
N_DROP_LONG = CL["dropped"]["longer than 72 h"]
STATIONS_N = ST["stations"]
HOURS, FEAT_ROWS = ST["hours"], ST["hours_with_features"]
KWH = "{0:,.2f}".format(ST["total_kwh"])
SITE_ZERO = "{0:.1f}%".format(ST["site_zero_share"] * 100)
STN_ZERO = "{0:.0f}%".format(ST["station_zero_share_mean"] * 100)
LOW_SHARE = "{0:.0f}%".format((SITE["demand_kwh"] < 1.0).mean() * 100)
TRAIN, TEST = SP["train_rows"], SP["test_rows"]
TEST_DAYS = round(TEST / 24)
DEMO_FROM, DEMO_TO = M["demo_week"]["start"], M["demo_week"]["end"]
DEMO_PHRASE = "{0}–{1}".format(int(DEMO_FROM[8:10]),
                               datetime.strptime(DEMO_TO[:10], "%Y-%m-%d").strftime("%d %B %Y").lstrip("0"))

B, LIN, RFX, RF = MOD["baseline"], MOD["linear"], MOD["rf_fixed"], MOD["rf"]
by_hour = M["error_breakdown"]["by_hour_of_day"]
worst = sorted(by_hour, key=lambda r: -r["rf_mae"])[:4]
best = sorted(by_hour, key=lambda r: r["rf_mae"])[:4]
WORST_H = hours_phrase([r["hour_of_day"] for r in worst])
BEST_H = hours_phrase([r["hour_of_day"] for r in best])
WORST_R = "{0:.1f}–{1:.1f}".format(min(r["rf_mae"] for r in worst), max(r["rf_mae"] for r in worst))
BEST_R = "{0:.1f}–{1:.1f}".format(min(r["rf_mae"] for r in best), max(r["rf_mae"] for r in best))
PEAK_ERR = M["peak_hours_mean_error"]
PEAK_WORD = "too low" if PEAK_ERR < 0 else "too high"
ABL_NOSHORT, ABL_CAL = ABL[1], ABL[2]

st_rows = [{
    "id": r["station_id"], "zero": float(r["zero_share"]), "base": float(r["baseline_mae"]),
    "lin": float(r["linear_mae"]), "rf": float(r["rf_mae"]), "gain": float(r["rf_improvement_pct"]),
} for r in STATIONS]
ST_RF = "{0:.2f}–{1:.2f}".format(min(r["rf"] for r in st_rows), max(r["rf"] for r in st_rows))
ST_BASE = "{0:.2f}–{1:.2f}".format(min(r["base"] for r in st_rows), max(r["base"] for r in st_rows))
ST_GAIN = "{0:.0f}–{1:.0f}%".format(min(r["gain"] for r in st_rows), max(r["gain"] for r in st_rows))
ST_ZERO = "{0:.0f}–{1:.0f}%".format(min(r["zero"] for r in st_rows) * 100, max(r["zero"] for r in st_rows) * 100)
LIN_WINS = [r["id"] for r in st_rows if r["lin"] < r["rf"]]
LIN_WINS_TXT = (" and ".join(LIN_WINS) if len(LIN_WINS) <= 2 else ", ".join(LIN_WINS))

TUNED_BEST_TXT = "{0} trees, min_samples_leaf = {1} and {2}".format(
    BEST["n_estimators"], BEST["min_samples_leaf"],
    "unlimited depth" if BEST["max_depth"] is None else "max_depth = {0}".format(BEST["max_depth"]))


# -------------------------------------------------------------------- helpers


def find_para(doc, prefix):
    hits = [p for p in doc.paragraphs if p.text.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit("Expected exactly one paragraph starting {0!r}, found {1}".format(prefix, len(hits)))
    return hits[0]


def drop_extra_runs(p, keep):
    for r in p.runs[keep:]:
        r._r.getparent().remove(r._r)


def set_text(p, text):
    """Replace a paragraph's text, keeping the first run's formatting."""
    if not p.runs:
        p.add_run(text)
        return
    p.runs[0].text = text
    drop_extra_runs(p, 1)


def set_body(p, body):
    """For 'Bold lead. body' paragraphs: keep the bold lead run, replace the rest."""
    assert len(p.runs) >= 2 and p.runs[0].bold, "not a bold-lead paragraph: " + p.text[:40]
    p.runs[1].text = body
    drop_extra_runs(p, 2)


def set_lead(p, lead, body):
    assert len(p.runs) >= 2 and p.runs[0].bold
    p.runs[0].text = lead
    p.runs[1].text = body
    drop_extra_runs(p, 2)


def set_cell(cell, text):
    set_text(cell.paragraphs[0], text)
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)


def fill_table(table, header, rows):
    """Rebuild a table as header + rows using its own first body row as the template."""
    template = copy.deepcopy(table.rows[1]._tr)
    for r in list(table.rows)[1:]:
        table._tbl.remove(r._tr)
    for i, text in enumerate(header):
        set_cell(table.rows[0].cells[i], text)
    for data in rows:
        tr = copy.deepcopy(template)
        table._tbl.append(tr)
        row = table.rows[-1]
        for i, text in enumerate(data):
            set_cell(row.cells[i], text)


def set_code(table, lines):
    """Replace the contents of a 1x1 code table, one paragraph per line."""
    cell = table.rows[0].cells[0]
    template = copy.deepcopy(cell.paragraphs[0]._p)
    for p in list(cell.paragraphs):
        p._p.getparent().remove(p._p)
    for line in lines:
        new = copy.deepcopy(template)
        cell._tc.append(new)
        para = docx.text.paragraph.Paragraph(new, cell)
        set_text(para, line)


def clone_paragraph_after(anchor_elm, source_p, text=None):
    new = copy.deepcopy(source_p._p)
    anchor_elm.addnext(new)
    para = docx.text.paragraph.Paragraph(new, source_p._parent)
    if text is not None:
        set_text(para, text)
    return para


def clone_table_after(anchor_elm, source_table, header, rows):
    new = copy.deepcopy(source_table._tbl)
    anchor_elm.addnext(new)
    table = Table(new, source_table._parent)
    fill_table(table, header, rows)
    return table


def replace_picture(doc, para, path, drop_old=True):
    """Swap the picture in `para` for the image at `path`, keeping its width.

    drop_old=False when `para` is a clone sharing its old image with another paragraph.
    """
    blip = para._p.xpath(".//a:blip")[0]
    old_rid = blip.get(qn("r:embed"))
    rid, _ = doc.part.get_or_add_image(path)
    blip.set(qn("r:embed"), rid)
    w, h = Image.open(path).size
    extent = para._p.xpath(".//wp:extent")[0]
    cx = int(extent.get("cx"))
    cy = int(round(cx * h / w))
    extent.set("cy", str(cy))
    for ext in para._p.xpath(".//a:xfrm/a:ext"):
        ext.set("cx", str(cx))
        ext.set("cy", str(cy))
    if drop_old:
        doc.part.drop_rel(old_rid)


def unique_docpr_id(doc, para):
    """Give a cloned drawing its own wp:docPr id (Word expects them to be unique)."""
    ids = [int(e.get("id")) for e in doc.element.body.xpath(".//wp:docPr")]
    para._p.xpath(".//wp:docPr")[0].set("id", str(max(ids) + 1))


def caption_after(para):
    nxt = para._p.getnext()
    return docx.text.paragraph.Paragraph(nxt, para._parent)


# ----------------------------------------------------------------------- main


def main():
    if not os.path.exists(BACKUP):
        shutil.copy2(REPORT, BACKUP)
        print("Backed up original report to", os.path.basename(BACKUP))
    doc = docx.Document(BACKUP)

    # ---- abstract -----------------------------------------------------------
    abstract = (
        "Station-Level EV Charging Demand Forecasting is a machine learning project that forecasts "
        "next-hour electric vehicle (EV) charging demand, in kWh per hour, for a workplace charging site. "
        "It uses {n:,} real charging sessions from {s} stations in the Caltech ACN-Data dataset, downloaded "
        "through its public REST API by a retry-safe script that saves every page as it arrives. An average "
        "station has no demand in {sz} of hours, so sessions are aggregated to whole-site hourly demand, "
        "giving {h:,} hourly rows between April and August 2018. Six backward-looking features (hour of day, "
        "weekday, and demand 1 h, 2 h, 24 h and 168 h earlier) are engineered and the data are split "
        "chronologically. Against a same-hour-last-week baseline (MAE {bm} kWh/h), Linear Regression reaches "
        "{lm} and a cross-validated Random Forest {rm} kWh/h ({rg} better) on {t} held-out hours; an ablation "
        "shows most of this gain comes from the 1 h and 2 h lags, and without them the gain falls to "
        "{ng}. A leakage test confirms the features look only backward. Results are served by a local web "
        "dashboard. The system is a decision-support prototype that under-predicts sharp peaks."
    ).format(n=N, s=STATIONS_N, sz=STN_ZERO, h=HOURS, bm=f2(B["mae"]), lm=f2(LIN["mae"]), rm=f2(RF["mae"]),
             rg=pct(RF["mae_improvement_pct"]), t=TEST, ng=pct(ABL_NOSHORT["mae_improvement_pct"]))
    words = len(abstract.split())
    print("Abstract: {0} words".format(words))
    if words > 200:
        raise SystemExit("Abstract exceeds the template's 200-word maximum")
    set_text(find_para(doc, "Station-Level EV Charging Demand Forecasting is a machine learning"), abstract)

    # ---- chapter 1 ----------------------------------------------------------
    set_text(find_para(doc, "The specific task is to reshape"),
             "The specific task is to reshape {n:,} charging sessions from {s} stations into an hourly site-level "
             "series, engineer lag features, train Linear Regression and Random Forest regressors, and compare "
             "their Mean Absolute Error (MAE) and Root Mean Squared Error (RMSE) in kWh/h against the baseline, "
             "with the predictions and metrics served through a local dashboard.".format(n=N, s=STATIONS_N))
    set_text(find_para(doc, "To ingest real EV charging sessions"),
             "To ingest real EV charging sessions from the Caltech ACN-Data REST API with a download script that "
             "retries after timeouts and saves every page as it arrives, and clean them into a tidy per-session table.")
    set_text(find_para(doc, "To aggregate sessions into whole-site"),
             "To aggregate sessions into station-wise and whole-site hourly demand (kWh/h), because an average "
             "station has no demand in about {0} of hours and is not predictable on its own.".format(STN_ZERO))
    set_text(find_para(doc, "To engineer backward-only features"),
             "To engineer backward-only features — hour, weekday, and demand 1 h, 2 h, 24 h and 168 h earlier — so "
             "that no future information leaks into any prediction, and to verify this with an automated test.")
    set_text(find_para(doc, "To split the data chronologically"),
             "To split the data chronologically (never shuffled), tune a Random Forest by time-series "
             "cross-validation, and compare it and Linear Regression against a same-hour-last-week baseline "
             "using MAE and RMSE.")
    set_text(find_para(doc, "The project covers next-hour"),
             "The project covers next-hour, site-level demand forecasting for the Caltech workplace charging site "
             "using {n:,} sessions (April–August 2018), six backward-looking features, two regression models, a naive "
             "baseline, MAE and RMSE evaluation, and a local dashboard. The five busiest stations are also modelled "
             "individually for comparison, but the project does not forecast every station and does not use "
             "weather, tariff or occupancy data. The results apply to the roughly three and a half months of data "
             "analysed and should not be generalised to other sites or seasons. Hourly demand is reconstructed from "
             "session records rather than metered, the model under-predicts sharp demand peaks, and the API "
             "connection can time out on long downloads, so the extract is limited to about {r:,} sessions and the "
             "downloader retries automatically.".format(n=N, r=N_RAW))

    # ---- chapter 2 ----------------------------------------------------------
    set_text(find_para(doc, "MAE is preferred to MAPE"),
             "MAE is preferred to MAPE when the series contains zero or near-zero values [6]; RMSE, which "
             "penalises large errors more heavily, is reported alongside it. The scikit-learn library [7], [8], [9] "
             "provides the models and metrics, pandas [10] the data handling, and matplotlib [11] the figures.")
    t_lit = doc.tables[6]
    set_cell(t_lit.rows[1].cells[3], "Source of the {0:,} sessions used here.".format(N))
    set_cell(t_lit.rows[2].cells[3], "Token-authenticated, 100 rows per page; can time out on long downloads.")
    set_cell(t_lit.rows[3].cells[3], "Baseline for weekly cycles; MAE {0} kWh/h.".format(f2(B["mae"])))
    set_cell(t_lit.rows[4].cells[3], "MAE {0} kWh/h ({1} better).".format(f2(LIN["mae"]), pct(LIN["mae_improvement_pct"])))
    set_cell(t_lit.rows[5].cells[3], "MAE {0} kWh/h ({1} better).".format(f2(RF["mae"]), pct(RF["mae_improvement_pct"])))
    set_cell(t_lit.rows[6].cells[2], "Series with zero or near-zero values")
    set_cell(t_lit.rows[6].cells[3], "MAPE is undefined at zero and unstable near it; MAE and RMSE used.")
    set_cell(t_lit.rows[7].cells[3], "Provides the models and the MAE and RMSE metrics.")

    # ---- chapter 3 ----------------------------------------------------------
    t_log = doc.tables[7]
    set_cell(t_log.rows[1].cells[2],
             "Framed the next-hour demand forecasting problem, set up the repository, and fetched sessions from the "
             "ACN-Data REST API with a downloader that retries after timeouts and saves each page to CSV as it arrives.")
    set_cell(t_log.rows[2].cells[2],
             "Cleaned sessions, spread each session’s kWh over its charging window, summed kWh per hour across "
             "stations with zero-filled quiet hours, and checked that total energy is conserved.")
    set_cell(t_log.rows[3].cells[3], "Completed; {0} better than baseline (MAE {1}).".format(
        pct(LIN["mae_improvement_pct"]), f2(LIN["mae"])))
    set_cell(t_log.rows[4].cells[3], "Completed; {0} better than baseline with fixed parameters.".format(
        pct(RFX["mae_improvement_pct"])))
    set_cell(t_log.rows[5].cells[2],
             "Tuned the Random Forest by time-series cross-validation, added RMSE, error analysis by hour, weekday and "
             "station, a feature ablation, the leakage test, README and requirements; report updated to the final run.")
    set_cell(t_log.rows[5].cells[3], "Completed; tuned forest {0} better than baseline.".format(pct(RF["mae_improvement_pct"])))

    set_text(find_para(doc, "Dataset source: Caltech"),
             "Dataset source: Caltech ACN-Data (https://ev.caltech.edu/acndata), fetched through its public "
             "token-authenticated REST API for the caltech site; a free API token is obtained by registering on the "
             "ACN-Data website, and the public demonstration token was used. The extract contains {r:,} downloaded "
             "sessions, of which {n:,} remain after cleaning, from {s} stations (25 April to 9 August 2018), reshaped "
             "into {h:,} hourly rows of whole-site demand ({f:,} after adding lag features).".format(
                 r=N_RAW, n=N, s=STATIONS_N, h=HOURS, f=FEAT_ROWS))
    t_req = doc.tables[8]
    set_cell(t_req.rows[3].cells[1], "pandas, NumPy, scikit-learn, requests, joblib, matplotlib (plots), Chart.js (dashboard charts)")
    t_cfg = doc.tables[9]
    set_cell(t_cfg.rows[1].cells[1],
             "Caltech ACN-Data; {n:,} cleaned sessions ({r:,} downloaded), {s} stations, April–August 2018; "
             "{h:,} hourly rows ({f:,} with lag features)".format(n=N, r=N_RAW, s=STATIONS_N, h=HOURS, f=FEAT_ROWS))
    set_cell(t_cfg.rows[2].cells[1],
             "Baseline (t − 168 h), LinearRegression, RandomForestRegressor(n_estimators={0}, min_samples_leaf={1}, "
             "max_depth={2}, random_state=42), chosen by TimeSeriesSplit cross-validation".format(
                 BEST["n_estimators"], BEST["min_samples_leaf"], BEST["max_depth"]))
    set_cell(t_cfg.rows[3].cells[1],
             "Each session’s energy spread over its charging window, hourly aggregation with zero-fill; "
             "backward-only lag features; chronological 80:20 split")
    set_cell(t_cfg.rows[4].cells[1],
             "Local web dashboard (Python http.server + Chart.js) at http://localhost:8000; /api/data returns JSON")
    set_text(find_para(doc, "The project is feasible"),
             "The project is feasible within the PBL timeframe (a nine-phase plan spread over about nine weeks) and "
             "the resources available to the team. Technically, the pipeline relies on mature open-source libraries "
             "(pandas, scikit-learn, requests, matplotlib) and a free public dataset, so no specialised infrastructure "
             "or licensing cost is required; it runs on a standard Windows 11 laptop with no GPU, and the whole "
             "pipeline — download, reshaping, tuning and training on about {f:,} hourly rows — completes in minutes. "
             "Operationally, the work divides cleanly into data acquisition, reshaping, feature engineering, modelling "
             "and dashboard stages, each implemented as its own script, which map onto the two-member team’s weekly "
             "milestones in Table 3.1; all nine phases are complete. Economically, only free tools and a free API "
             "token are needed, so no budget is required. The main risks were the API connection timing out on long "
             "downloads, the sparsity of individual stations, and the short history of under four months; these were "
             "mitigated by a downloader that retries and resumes, limited to about {r:,} sessions, by forecasting the "
             "whole site instead of single stations, and by using a chronological split with MAE and RMSE as the "
             "metrics.".format(f=FEAT_ROWS, r=N_RAW))

    # ---- chapter 4 ----------------------------------------------------------
    set_text(find_para(doc, "The pipeline is organised"),
             "The pipeline is organised as five one-way layers (Figure 4.1). The external source is the Caltech "
             "ACN-Data REST API (token authentication, 100 sessions per page). The ingestion layer downloads the "
             "sessions page by page, retrying each page up to five times, and appends every page to a CSV file "
             "immediately so that a timeout cannot lose data already received; a second script cleans the file into "
             "a tidy sessions table (station, connection, disconnection and charge-end time, kWh) in Pacific time. "
             "The transformation layer spreads each session’s energy over the hours of its charging window, sums it "
             "per hour for every station and for the whole site, and zero-fills hours with no charging, giving {h:,} "
             "hourly rows; a validation check confirms that total energy is unchanged by the reshaping ({k} kWh in "
             "and out). The modelling layer builds backward-only features and trains the baseline, Linear Regression "
             "and a cross-validated Random Forest. The presentation layer reports MAE and RMSE against the baseline, "
             "runs the leakage test, and serves the results as JSON from /api/data to a browser dashboard.".format(
                 h=HOURS, k=KWH))
    set_text(find_para(doc, "The simplest version fetches"),
             "The simplest version fetches the sessions, reshapes them into an hourly site-level series, and "
             "evaluates the naive rule “demand at hour t equals demand at hour t − 168” (same hour last week) on the "
             "most recent 20% of hours. This baseline and the evaluation harness were built before any machine "
             "learning model so that every later model has a fixed bar to beat. On the {t} held-out hours the "
             "baseline gives MAE = {m} kWh/h and RMSE = {r} kWh/h.".format(t=TEST, m=f2(B["mae"]), r=f2(B["rmse"])))
    set_text(find_para(doc, "Linear Regression was then added"),
             "Linear Regression was then added as the first learned model on six backward-only features, giving "
             "MAE = {m} kWh/h (RMSE {r}), which is {g} better than the baseline.".format(
                 m=f2(LIN["mae"]), r=f2(LIN["rmse"]), g=pct(LIN["mae_improvement_pct"])))
    p_iter2 = find_para(doc, "The Linear Regression model was complemented")
    set_text(p_iter2,
             "The Linear Regression model was complemented by a Random Forest regressor with fixed hyperparameters "
             "(300 trees, min_samples_leaf = 2) on the same six backward-only features (hour, weekday, lag_1h, lag_2h, "
             "lag_24h, lag_168h). The Random Forest can model nonlinear interactions — for example, a morning arrival "
             "peak that depends on both the hour of day and the demand an hour earlier — and reduced MAE to {m} kWh/h, "
             "a {g} improvement over the baseline. A local web dashboard was then added to serve actual and predicted "
             "demand and the summary metrics as JSON from /api/data.".format(m=f2(RFX["mae"]), g=pct(RFX["mae_improvement_pct"])))
    # Iteration 3 paragraph, cloned from a bold-lead paragraph so the lead stays bold.
    template_lead = find_para(doc, "Training details.")
    iter3 = clone_paragraph_after(p_iter2._p, template_lead)
    set_lead(iter3, "Iteration 3 — cross-validated tuning. ",
             "A small grid (n_estimators 200 or 300, min_samples_leaf 1, 2 or 4, max_depth unlimited or 12) was "
             "searched with four-fold TimeSeriesSplit cross-validation on the training period only, so the test hours "
             "played no part in the choice. The search selected {b} (cross-validated MAE {cv} kWh/h). On the held-out "
             "hours this tuned forest scored MAE {tm} kWh/h, essentially the same as the fixed-parameter forest "
             "({fm}), so tuning did not improve the test score. It is kept as the final model because the selection "
             "rule was fixed in advance and does not look at the test period.".format(
                 b=TUNED_BEST_TXT, cv=f2(M["tuning"]["cv_mae"]), tm=f2(RF["mae"]), fm=f2(RFX["mae"])))

    set_text(find_para(doc, "The final approach is a Random Forest"),
             "The final approach is a Random Forest regressor that predicts site-level kWh in hour t from six clues "
             "that all look backward: hour of day, weekday, and demand 1 h, 2 h, 24 h and 168 h earlier. Random "
             "Forest builds many decision trees and averages their predictions; the final model uses n_estimators={ne}, "
             "min_samples_leaf={ml}, max_depth={md} and random_state=42. In the final run the most useful clues were "
             "demand one hour earlier (importance {a:.3f}), demand one week earlier ({b:.3f}), hour of day ({c:.3f}) "
             "and demand one day earlier ({d:.3f}), which reflects that a car that began charging in the last hour is "
             "usually still charging, on top of a regular daily and weekly rhythm.".format(
                 ne=BEST["n_estimators"], ml=BEST["min_samples_leaf"], md=BEST["max_depth"],
                 a=IMP["lag_1h"], b=IMP["lag_168h"], c=IMP["hour_of_day"], d=IMP["lag_24h"]))

    set_text(find_para(doc, "Sort the rows by time"),
             "Sort the rows by time and split chronologically: the earliest 80% for training ({tr:,} rows, {a} to {b}) "
             "and the latest 20% for testing ({te:,} rows, {c} to {d}); the data are never shuffled.".format(
                 tr=TRAIN, te=TEST, a=dmy(SP["train_start"]), b=dmy(SP["train_end"]),
                 c=dmy(SP["test_start"]), d=dmy(SP["test_end"])))
    set_text(find_para(doc, "Fit RandomForestRegressor"),
             "Fit RandomForestRegressor twice on the training rows: once with fixed settings (300 trees, "
             "min_samples_leaf=2, random_state=42), and once with settings chosen by GridSearchCV over "
             "TimeSeriesSplit (4 folds), scored by MAE on the training period only.")
    set_text(find_para(doc, "Predict the test rows"),
             "Predict the test rows and score every model with MAE and RMSE in kWh/h (MAPE is not used because "
             "demand is zero or close to zero in many hours).")
    set_text(find_para(doc, "Save the Random Forest"),
             "Save the final (tuned) Random Forest (joblib), the metrics (metrics.json) and the test predictions "
             "(predictions.csv) for the dashboard, together with the error by hour and weekday, the feature ablation, "
             "and the per-station results for the five busiest stations.")
    set_body(find_para(doc, "Training details."),
             "A single chronological train/test split is used; model selection uses four-fold TimeSeriesSplit "
             "cross-validation inside the training period, so there is no separate validation set. Hyperparameters "
             "were tuned over a 12-combination grid; no feature scaling is applied; and no loss function, optimizer "
             "or epochs are involved because Linear Regression uses least squares and Random Forest does not use "
             "epoch-based gradient training.")

    # ---- chapter 5 ----------------------------------------------------------
    set_body(find_para(doc, "Data Ingestion Module"),
             "pages through the ACN-Data REST API with the API token (100 sessions per request) until about {r:,} "
             "sessions are saved. Each page is retried up to five times, waiting between one and five minutes after a "
             "failure, and is appended to raw_sessions.csv and flushed to disk before the next request; progress is "
             "recorded in download_state.json so an interrupted run resumes where it stopped. The second script "
             "cleans the raw file into a tidy per-session table (station_id, connection_time, disconnect_time, "
             "charge_end_time, kwh_delivered), converts times to Pacific time, and drops duplicates, malformed "
             "records, non-positive energy, sessions that end before they start, and sessions longer than 72 hours "
             "({d} of {r:,}).".format(r=N_RAW, d=N_DROP_LONG))
    set_body(find_para(doc, "Hourly Aggregation and Feature Module"),
             "spreads each session’s kWh evenly across the hours of its charging window (connection to charge-end "
             "time), sums it per hour for every station and for the whole site, and zero-fills hours with no "
             "charging. It asserts that total energy is unchanged, writes the station-wise and site-wide hourly "
             "tables and the dataset statistics, then adds hour, weekday and lag features (1 h, 2 h, 24 h, 168 h) "
             "through one shared function and drops the first week that lacks full lag history.")
    set_body(find_para(doc, "Model Training Module"),
             "splits the hourly table chronologically (80:20), evaluates the same-hour-last-week baseline, trains "
             "Linear Regression and a fixed-parameter Random Forest, tunes a second Random Forest by time-series "
             "cross-validation, and scores each with MAE and RMSE. It also computes error by hour of day and weekday, "
             "a feature ablation, and per-station results for the five busiest stations, and saves the final model, "
             "metrics.json and predictions.csv.")
    set_body(find_para(doc, "Dashboard Module"),
             "a Python http.server application on localhost:8000 that serves a plain HTML/JavaScript page (Chart.js, "
             "bundled so it works offline) at / and a JSON payload at /api/data combining predictions.csv, "
             "metrics.json, stats.json and station_results.csv. It listens on the local machine only.")
    set_lead(find_para(doc, "Test Data Utility"),
             "Test and Utility Scripts (test_no_leakage.py, generate_dataset.py, make_figures.py): ",
             "test_no_leakage.py checks that every lag feature equals demand at the earlier hour, that the training "
             "period ends before the test period begins, that the checker detects a deliberately leaky feature, and "
             "that a forest trained on shuffled targets is clearly worse. generate_dataset.py creates a small "
             "synthetic raw_sessions.csv in the API’s schema so the pipeline can be tried without a token (it is not "
             "used for any reported result), and make_figures.py redraws the report figures from the pipeline’s output.")

    set_text(find_para(doc, "(b)  Backward-only"), "(b)  Backward-only feature engineering (common.py)")
    set_text(find_para(doc, "(d)  Training the models"),
             "(d)  Tuning by cross-validation, training and scoring the models (4_train_model.py)")
    set_text(find_para(doc, "(a)  Reshaping"), "(a)  Reshaping sessions into zero-filled hourly demand (3_build_hourly.py)")
    set_code(doc.tables[10], [
        "# spread each session's energy over the hours of its charging window",
        "span = pd.date_range(start.floor('h'), end.ceil('h'), freq='h')[:-1]",
        "share = energy / len(span)",
        "# ... one (station, hour, share) row per hour; then sum and zero-fill",
        "wide = (spread.groupby(['hour', 'station_id'])['demand_kwh'].sum()",
        "              .unstack('station_id')",
        "              .reindex(full_range, fill_value=0.0).fillna(0.0))",
        "site = wide.sum(axis=1)",
        "# validation gate: reshaping must neither lose nor create energy",
        "assert abs(energy_in - energy_out) < 1e-6",
    ])
    set_code(doc.tables[11], [
        "out['hour_of_day'] = out['hour'].dt.hour",
        "out['weekday']     = out['hour'].dt.weekday",
        "# every lag looks strictly backward -- no future information can leak",
        "for lag in (1, 2, 24, 168):",
        "    out[f'lag_{lag}h'] = out['demand_kwh'].shift(lag)",
        "# drop the first week, which has no complete 168-hour history",
        "out = out.dropna()",
    ])
    set_code(doc.tables[12], [
        "FEATURES = ['hour_of_day', 'weekday', 'lag_1h', 'lag_2h',",
        "            'lag_24h', 'lag_168h']",
        "df = df.sort_values('hour')          # time order, never shuffled",
        "split = int(len(df) * 0.8)",
        "train, test = df.iloc[:split], df.iloc[split:]",
        "# baseline: repeat the value from the same hour last week",
        "baseline_mae = mean_absolute_error(test['demand_kwh'], test['lag_168h'])",
    ])
    set_code(doc.tables[13], [
        "lin = LinearRegression().fit(X_train, y_train)",
        "search = GridSearchCV(RandomForestRegressor(random_state=42),",
        "    {'n_estimators': [200, 300], 'min_samples_leaf': [1, 2, 4],",
        "     'max_depth': [None, 12]},",
        "    cv=TimeSeriesSplit(n_splits=4),",
        "    scoring='neg_mean_absolute_error').fit(X_train, y_train)",
        "rf = RandomForestRegressor(random_state=42, **search.best_params_)",
        "rf.fit(X_train, y_train)      # parameters chosen on the training period only",
        "mae  = mean_absolute_error(y_test, rf.predict(X_test))",
        "rmse = np.sqrt(mean_squared_error(y_test, rf.predict(X_test)))",
        "joblib.dump(rf, 'models/random_forest.joblib')",
    ])

    set_text(find_para(doc, "The prototype is a local web dashboard"),
             "The prototype is a local web page (http://localhost:8000) started with 5_dashboard_server.py. It "
             "shows the MAE and RMSE of the baseline, Linear Regression and both Random Forests, a line chart of "
             "actual versus predicted demand on the test hours with a week-window selector, the error by hour of day, "
             "the feature importances, the feature ablation, and a table for the five busiest stations; the same data "
             "are available as JSON at /api/data (Figure 5.5). The other figures show the data reshaping, the "
             "pipeline run, the code layout and the phase status against the review timeline.")

    # ---- figures ------------------------------------------------------------
    fig_map = [("Figure 4.1", "fig_4_1.png"), ("Figure 5.1", "fig_5_1.png"), ("Figure 5.2", "fig_5_2.png"),
               ("Figure 5.3", "fig_5_3.png"), ("Figure 5.4", "fig_5_4.png"), ("Figure 6.1", "fig_6_1.png")]
    for caption_prefix, filename in fig_map:
        cap = find_para(doc, caption_prefix + "  ")
        picture_para = docx.text.paragraph.Paragraph(cap._p.getprevious(), cap._parent)
        replace_picture(doc, picture_para, os.path.join(FIG, filename))

    set_text(find_para(doc, "Figure 5.1  "),
             "Figure 5.1  Cleaned session log ({n:,} sessions, {s} stations) reshaped into the hourly site-level "
             "table ({h:,} hours)".format(n=N, s=STATIONS_N, h=HOURS))
    set_text(find_para(doc, "Figure 5.2  "),
             "Figure 5.2  Pipeline output — energy check passed, Random Forest MAE {r} vs baseline {b} kWh/h, "
             "leakage test passed".format(r=f2(RF["mae"]), b=f2(B["mae"])))
    set_text(find_para(doc, "Figure 5.4  "), "Figure 5.4  Phase status against the review timeline (all nine phases completed)")
    set_text(find_para(doc, "Figure 6.1  "),
             "Figure 6.1  Test-set MAE and RMSE of the baseline, Linear Regression and both Random Forests (top) and "
             "Random Forest forecast versus actual demand for one representative test week, {d} (bottom)".format(d=DEMO_PHRASE))

    # Figure 5.5: dashboard screenshot, cloned from Figure 5.4's picture and caption.
    cap54 = find_para(doc, "Figure 5.4  ")
    pic54 = docx.text.paragraph.Paragraph(cap54._p.getprevious(), cap54._parent)
    pic55 = clone_paragraph_after(cap54._p, pic54)
    replace_picture(doc, pic55, os.path.join(FIG, "fig_5_5.png"), drop_old=False)
    unique_docpr_id(doc, pic55)
    # widen the cloned picture to a readable size while keeping its aspect ratio
    extent = pic55._p.xpath(".//wp:extent")[0]
    w_emu = int(5.6 * 914400)
    pw, ph = Image.open(os.path.join(FIG, "fig_5_5.png")).size
    extent.set("cx", str(w_emu))
    extent.set("cy", str(int(w_emu * ph / pw)))
    for ext in pic55._p.xpath(".//a:xfrm/a:ext"):
        ext.set("cx", str(w_emu))
        ext.set("cy", str(int(w_emu * ph / pw)))
    clone_paragraph_after(pic55._p, cap54,
                          "Figure 5.5  Local dashboard at http://localhost:8000: accuracy cards, forecast versus actual, "
                          "error by hour of day and feature importance")

    # ---- chapter 6 ----------------------------------------------------------
    set_text(find_para(doc, "MAE (Mean Absolute Error)"),
             "MAE (Mean Absolute Error) is the average absolute difference between the actual and the predicted "
             "hourly demand, in kWh/h; lower is better and it is expressed in the same units as demand. RMSE (Root "
             "Mean Squared Error) is the square root of the average squared difference; it is also in kWh/h but "
             "penalises large misses more heavily, so a wide gap between RMSE and MAE signals occasional large "
             "errors. MAE is the headline metric and MAPE is not used: demand is zero or close to zero in many "
             "hours (the site series is exactly zero in {z} of hours and below 1 kWh/h in {lo}), where percentage "
             "error is undefined or explodes. Improvement over the baseline is (baseline MAE − model MAE) / baseline "
             "MAE × 100. All metrics are computed on the held-out, chronologically latest 20% of hours "
             "({t} rows).".format(z=SITE_ZERO, lo=LOW_SHARE, t=TEST))

    fill_table(doc.tables[14],
               ["Model", "MAE (kWh/h)", "RMSE (kWh/h)", "Improvement vs. baseline (MAE)"],
               [["Baseline (same hour last week) — reference rule", f2(B["mae"]), f2(B["rmse"]), "—"],
                ["Linear Regression — learned on lag features", f2(LIN["mae"]), f2(LIN["rmse"]), pct(LIN["mae_improvement_pct"])],
                ["Random Forest (fixed parameters)", f2(RFX["mae"]), f2(RFX["rmse"]), pct(RFX["mae_improvement_pct"])],
                ["Random Forest (cross-validated, final)", f2(RF["mae"]), f2(RF["rmse"]), pct(RF["mae_improvement_pct"])]])

    set_text(find_para(doc, "The evaluation respects time"),
             "The evaluation respects time: the data are split chronologically and every feature looks backward, which "
             "test_no_leakage.py verifies, so the models are tested strictly on the future. Linear Regression improves "
             "on the baseline by {lg} and the Random Forest by about {rg} (MAE {rm} versus {bm} kWh/h; RMSE {rr} "
             "versus {br} kWh/h), so recent demand carries far more information than repeating last week’s value. "
             "Feature importance ranks demand one hour earlier first ({a:.3f}), followed by demand one week earlier "
             "({b:.3f}), hour of day ({c:.3f}) and demand one day earlier ({d:.3f}). Errors are largest around "
             "{wh} (MAE {wr} kWh/h) and smallest around {bh} ({brr} kWh/h). In Figure 6.1 the forecast follows the "
             "daily cycle closely but under-predicts the sharpest peaks: over the busiest 10% of test hours it is on "
             "average {pe:.1f} kWh/h {pw}.".format(
                 lg=pct(LIN["mae_improvement_pct"]), rg=pct(RF["mae_improvement_pct"]), rm=f2(RF["mae"]),
                 bm=f2(B["mae"]), rr=f2(RF["rmse"]), br=f2(B["rmse"]), a=IMP["lag_1h"], b=IMP["lag_168h"],
                 c=IMP["hour_of_day"], d=IMP["lag_24h"], wh=WORST_H, wr=WORST_R, bh=BEST_H, brr=BEST_R,
                 pe=abs(PEAK_ERR), pw=PEAK_WORD))

    p172 = find_para(doc, "The improvement should be read with care")
    set_text(p172,
             "The improvement should be read with care, and the feature ablation in Table 6.2 shows why. Hourly "
             "demand is reconstructed from sessions by spreading each session’s energy over its charging window, which "
             "uses the session’s end time; a live meter would not know that in advance, so the 1 h and 2 h lags can "
             "carry somewhat more than they would in deployment. With those two lags removed the forest scores MAE "
             "{nm} kWh/h, still {ng} better than the baseline, and calendar features alone (hour and weekday) reach "
             "{cm} kWh/h ({cg} better). The conservative reading is therefore that the forest beats last week’s value "
             "by roughly {lo}–{hi}% without the recent lags, and the {rg} figure is an upper bound that depends on "
             "the demand reconstruction. Tuning did not help either: the cross-validated forest ({tm}) is marginally "
             "worse than the fixed-parameter one ({fm}), a difference too small to mean anything on {t} hours, and no "
             "significance test was run.".format(
                 nm=f2(ABL_NOSHORT["mae"]), ng=pct(ABL_NOSHORT["mae_improvement_pct"]), cm=f2(ABL_CAL["mae"]),
                 cg=pct(ABL_CAL["mae_improvement_pct"]),
                 lo=int(min(ABL_NOSHORT["mae_improvement_pct"], ABL_CAL["mae_improvement_pct"]) + 0.5),
                 hi=int(max(ABL_NOSHORT["mae_improvement_pct"], ABL_CAL["mae_improvement_pct"]) + 0.5),
                 rg=pct(RF["mae_improvement_pct"]), tm=f2(RF["mae"]), fm=f2(RFX["mae"]), t=TEST))

    # Table 6.2 (ablation) directly after the paragraph that cites it
    caption_src = find_para(doc, "Table 6.1  ")
    spacer_src = docx.text.paragraph.Paragraph(doc.tables[14]._tbl.getnext(), caption_src._parent)
    cap62 = clone_paragraph_after(p172._p, caption_src,
                                  "Table 6.2  Feature ablation: the final Random Forest refitted on feature subsets ({0} test hours)".format(TEST))
    t62 = clone_table_after(cap62._p, doc.tables[14],
                            ["Features used", "MAE (kWh/h)", "RMSE (kWh/h)", "Improvement vs. baseline (MAE)"],
                            [[a["features"], f2(a["mae"]), f2(a["rmse"]), pct(a["mae_improvement_pct"])] for a in ABL])
    sp62 = clone_paragraph_after(t62._tbl, spacer_src)

    p173 = find_para(doc, "The pipeline also checks")
    set_text(p173,
             "The pipeline also checks that total energy is conserved when sessions are reshaped into hourly demand "
             "({k} kWh in the sessions and in the hourly table), so the aggregation step neither loses nor creates "
             "energy.".format(k=KWH))
    p_station = clone_paragraph_after(p173._p, p173,
                                      "Fitting the same settings to each of the five busiest stations (Table 6.3) gives an MAE of "
                                      "{rf} kWh/h against {bs} kWh/h for each station’s own same-hour-last-week baseline ({g} better), "
                                      "but these stations still have no demand in {z} of hours and the absolute numbers are small, so "
                                      "station-level forecasts are far less useful than the site total. Linear Regression is as good or "
                                      "better than the forest at {nl} of the five stations ({ids}), which is typical of sparse series where "
                                      "a simple model is hard to beat.".format(
                                          rf=ST_RF, bs=ST_BASE, g=ST_GAIN, z=ST_ZERO, nl=len(LIN_WINS), ids=LIN_WINS_TXT))
    cap63 = clone_paragraph_after(p_station._p, caption_src,
                                  "Table 6.3  The five busiest stations, each with its own model and its own same-hour-last-week baseline")
    t63 = clone_table_after(cap63._p, doc.tables[14],
                            ["Station", "Zero-demand hours", "MAE: baseline / Random Forest (kWh/h)", "Improvement vs. baseline (MAE)"],
                            [[r["id"], "{0:.1f}%".format(r["zero"] * 100), "{0} / {1}".format(f2(r["base"]), f2(r["rf"])),
                              pct(r["gain"])] for r in st_rows])
    clone_paragraph_after(t63._tbl, spacer_src)

    set_text(find_para(doc, "The main limitations"),
             "The main limitations are the short history (about three and a half months, April–August 2018, from a "
             "single Caltech site), a test period of only {t} hours (about {d} days), and the sparsity of individual "
             "stations (about {sz} zero-demand hours on average), which is why the headline model forecasts "
             "site-level demand. Hourly demand is reconstructed by spreading each session’s energy evenly over its "
             "charging window, which smooths real charging curves and uses information (the session’s end time) that "
             "a live system would not have; this inflates the value of the 1 h and 2 h lags, as the ablation shows. "
             "The Random Forest under-predicts sharp demand peaks. Tuning was a small 12-combination grid and did not "
             "improve the held-out score, and no significance test or repeated cross-validation of the final "
             "comparison was run. The download is limited to about {r:,} sessions, and the ACN-Data API can time out "
             "on long requests, which the retry logic handles but which limits how much data is practical to fetch. "
             "Results may not transfer to other sites, seasons or years, and external factors such as weather, "
             "holidays, tariffs and fleet changes are not represented in the features.".format(
                 t=TEST, d=TEST_DAYS, sz=STN_ZERO, r=N_RAW))

    # ---- chapter 7 ----------------------------------------------------------
    set_body(find_para(doc, "M PRAVEEN VIGNESH: "),
             "Worked on data acquisition from the ACN-Data API, the retry-safe downloader, reshaping sessions into the "
             "hourly series, and backward-only feature engineering. Learned how a raw transaction log becomes a "
             "forecasting-ready time series and why every feature must look only backward to avoid leakage. A key "
             "challenge was the API connection timing out on long downloads, which led to retrying each page and "
             "saving it to disk as it arrives.")
    set_body(find_para(doc, "J FARHAN SOHAIL: "),
             "Worked on model training, MAE and RMSE evaluation against the baseline, the local dashboard, and "
             "documentation. Learned why a time-ordered split and MAE (rather than MAPE) are appropriate for demand "
             "data containing zero and near-zero hours. A key challenge was the model under-predicting sharp demand "
             "peaks.")
    set_text(find_para(doc, "The project divides the work"),
             "The project divides the work into data acquisition, reshaping, modelling, dashboard and documentation, "
             "with one script per pipeline stage, which made it easy to share work and test each stage on its own. A "
             "useful team practice was building the baseline and MAE harness first, so every later change could be "
             "judged against a fixed bar, and keeping all features strictly backward-looking, which the leakage test "
             "now checks automatically. If the PBL cycle were restarted, more time would be reserved for gradient "
             "boosting, a longer history from more sites, and a demand series built from metered power rather than "
             "reconstructed from sessions, because the feature ablation showed how much the headline result depends "
             "on that reconstruction. Mentor feedback should be recorded against the actual review meetings; no "
             "specific mentor comments were supplied, so none are attributed here.")
    set_body(find_para(doc, "Data handling:"),
             "the project demonstrates fetching data from a REST API with timeout-safe retries and incremental "
             "saving, cleaning session records, reshaping them into station-wise and site-wide hourly time series, and "
             "validating the reshaping with an energy-conservation check.")
    set_body(find_para(doc, "Machine learning:"),
             "it applies lag-feature engineering, chronological train-test splitting, time-series cross-validation "
             "for hyperparameter tuning, Linear Regression and Random Forest regression, and comparison with a naive "
             "baseline.")
    set_body(find_para(doc, "Evaluation:"),
             "it uses MAE and RMSE on held-out future hours, justifies MAE over MAPE for data with zero and near-zero "
             "hours, breaks the error down by hour, weekday and station, and uses a feature ablation to show where the "
             "accuracy comes from.")
    set_body(find_para(doc, "Software integration:"),
             "it connects the trained model’s predictions to a local web dashboard through a JSON API and covers the "
             "feature pipeline with an automated leakage test.")

    # ---- chapter 8 ----------------------------------------------------------
    set_text(find_para(doc, "The project built a complete pipeline"),
             "The project built a complete pipeline that forecasts next-hour, site-level EV charging demand for the "
             "Caltech workplace charging site from {n:,} real sessions across {s} stations. A retry-safe script "
             "downloads the sessions, which are reshaped into {h:,} hourly rows; six backward-only features are "
             "engineered and the data are split chronologically. On {t} held-out hours the cross-validated Random "
             "Forest achieved an MAE of {rm} kWh/h and an RMSE of {rr} kWh/h, {rg} better in MAE than the "
             "same-hour-last-week baseline ({bm} kWh/h), and Linear Regression achieved {lm} kWh/h ({lg} better). The "
             "predictions are served through a local dashboard at http://localhost:8000. The work answers the driving "
             "question for this dataset: learned models using lag features beat the naive rule, mainly through "
             "recent-hour demand. Without the 1 h and 2 h lags the Random Forest’s gain falls to {ng} (MAE {nm} kWh/h), "
             "and the model still under-predicts sharp peaks.".format(
                 n=N, s=STATIONS_N, h=HOURS, t=TEST, rm=f2(RF["mae"]), rr=f2(RF["rmse"]), rg=pct(RF["mae_improvement_pct"]),
                 bm=f2(B["mae"]), lm=f2(LIN["mae"]), lg=pct(LIN["mae_improvement_pct"]),
                 ng=pct(ABL_NOSHORT["mae_improvement_pct"]), nm=f2(ABL_NOSHORT["mae"])))
    set_text(find_para(doc, "Optimise the model"),
             "Try Gradient Boosting and a wider tuning search, and test whether the differences between models are "
             "statistically meaningful with repeated time-series cross-validation.")
    set_text(find_para(doc, "Extend the evaluation"),
             "Build the demand series from metered power (the ACN-Data time-series endpoint) instead of reconstructing "
             "it from session records, to remove the look-ahead in the short lags.")
    set_text(find_para(doc, "Add a test that proves"),
             "Forecast further ahead than one hour, where the short lags cannot be used, and extend the per-station "
             "models beyond the five busiest stations.")

    # ---- appendix -----------------------------------------------------------
    a1 = find_para(doc, "A.1  Full source code")
    link = a1.runs[1].text[a1.runs[1].text.index("GitHub:"):]
    set_body(a1, "common.py, 1_fetch_raw.py, 2_build_sessions.py, 3_build_hourly.py, 4_train_model.py, "
                 "5_dashboard_server.py, test_no_leakage.py, make_figures.py, generate_dataset.py, the web/ folder, "
                 "README.md and requirements.txt. " + link)

    # ---- contents tables ------------------------------------------------------
    lof = doc.tables[3]
    titles = {"5.1": "Cleaned session log reshaped into the hourly site-level table",
              "5.2": "Pipeline output — energy check, MAE and leakage test",
              "6.1": "Test-set MAE and RMSE by model, and Random Forest forecast versus actual demand"}
    for row in lof.rows[1:]:
        key = row.cells[0].text.strip()
        if key in titles:
            set_cell(row.cells[1], titles[key])
    i54 = [i for i, r in enumerate(lof.rows) if r.cells[0].text.strip() == "5.4"][0]
    lof.rows[i54]._tr.addnext(copy.deepcopy(lof.rows[i54]._tr))
    r55 = lof.rows[i54 + 1]
    set_cell(r55.cells[0], "5.5")
    set_cell(r55.cells[1], "Local web dashboard (localhost:8000)")

    lot = doc.tables[4]
    idx = [i for i, r in enumerate(lot.rows) if r.cells[0].text.strip() == "6.1"][0]
    row61 = lot.rows[idx]
    for offset, (num, title) in enumerate([("6.3", "The five busiest stations, each with its own model"),
                                           ("6.2", "Feature ablation of the final Random Forest")]):
        clone = copy.deepcopy(row61._tr)
        row61._tr.addnext(clone)
        row = lot.rows[idx + 1]
        set_cell(row.cells[0], num)
        set_cell(row.cells[1], title)

    doc.save(REPORT)
    print("Saved", REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
