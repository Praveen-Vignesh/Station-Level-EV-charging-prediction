# Station-Level EV Charging Demand Forecasting

Forecasts **next-hour EV charging demand (kWh per hour)** for the Caltech workplace
charging site from real [ACN-Data](https://ev.caltech.edu/dataset) sessions, and compares
Linear Regression and a Random Forest against a "same hour last week" baseline.
Everything runs locally; the results are shown on a small web page at `localhost:8000`.

## Run it

```
pip install -r requirements.txt

python 1_fetch_raw.py          # download ~6,500 sessions  (about a minute)
python 2_build_sessions.py     # clean -> data/sessions.csv
python 3_build_hourly.py       # station-wise + site hourly tables, features
python 4_train_model.py        # baseline, Linear Regression, Random Forest, tuning
python test_no_leakage.py      # prove no future information reaches the model
python 5_dashboard_server.py   # then open http://localhost:8000
```

`python make_figures.py` redraws the report figures from the same output files.

No API token is required: the public `DEMO_TOKEN` works for the `caltech` site. To use your
own, `set ACN_API_TOKEN=...` (Windows) or `export ACN_API_TOKEN=...` first.

## The downloader (`1_fetch_raw.py`)

The ACN-Data API sometimes times out partway through a long download. The script is built so
that never costs you data:

- each page is **appended to `data/raw_sessions.csv` and fsynced immediately**, so a crash
  loses at most the page in flight;
- progress is saved in `data/download_state.json`, so **re-running resumes** where it stopped;
- every page is **retried up to 5 times**, waiting 1, 2, 3, 5 and 5 minutes between attempts.

```
python 1_fetch_raw.py --rows 3000   # different size
python 1_fetch_raw.py --restart     # throw away progress and start over
```

No token or network? `python generate_dataset.py` writes a small *synthetic* raw file so
scripts 2-5 can be tried. It refuses to overwrite a real download, and nothing reported
comes from it.

## What each stage does

| Script | Input -> output |
|---|---|
| `1_fetch_raw.py` | ACN-Data API -> `data/raw_sessions.csv` |
| `2_build_sessions.py` | drops invalid rows, converts UTC to Pacific -> `data/sessions.csv` |
| `3_build_hourly.py` | spreads each session's kWh over its charging window -> `hourly_station.csv`, `hourly_site.csv`, `hourly.csv`, `stats.json`. Asserts total kWh in = total kWh out. |
| `4_train_model.py` | chronological 80/20 split; baseline, Linear Regression, Random Forest; cross-validated tuning; MAE and RMSE; error by hour and weekday; feature ablation; the busiest 5 stations -> `models/`, `metrics.json`, `predictions.csv`, `station_results.csv` |
| `test_no_leakage.py` | five checks that the features only look backward |
| `5_dashboard_server.py` | serves `web/` and `/api/data` on `127.0.0.1:8000` |

`common.py` holds the constants and the single feature builder used by all of them.

## Method in brief

- **Target:** whole-site kWh in each hour. Individual stations are idle most of the time
  (about 79% zero-demand hours on average), so a per-station series carries little signal.
  The busiest five stations are modelled separately for comparison.
- **Features (six, all backward-looking):** hour of day, weekday, and demand 1 h, 2 h, 24 h and
  168 h earlier.
- **Evaluation:** the earliest 80% of hours train, the latest 20% test; never shuffled.
  Hyperparameters are chosen by `TimeSeriesSplit` cross-validation *inside the training
  period only*.
- **Baseline:** demand at the same hour one week earlier (`lag_168h`).

## Caveats

- Hourly demand is **reconstructed from sessions**: each session's energy is spread evenly
  over the hours between plug-in and its `doneChargingTime`. A live meter would not know a
  session's end time in advance, so the 1 h and 2 h lags can carry slightly more than they
  would in deployment. `4_train_model.py` therefore also reports the forest **without** those
  lags (see "Where the accuracy comes from" on the dashboard).
- About three months of data (April to August 2018) from one site; results should not be
  assumed to transfer to other sites or seasons.
- Decision-support prototype, not an operational controller.
