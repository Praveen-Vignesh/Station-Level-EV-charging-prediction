// Dashboard front end: fetches /api/data once and draws everything from it.
(function () {
  "use strict";

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = (id) => document.getElementById(id);
  const fmt = (x, d = 2) => Number(x).toFixed(d);
  const pct = (x) => (x >= 0 ? "+" : "") + Number(x).toFixed(1) + "%";

  let data = null;
  let forecastChart = null;
  let pendingStart = null;   // slider position to apply once its max is known

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(message) {
    const box = $("error");
    box.textContent = message;
    box.hidden = false;
    $("subtitle").textContent = "No results to show yet";
  }

  // ---- header + metric cards ------------------------------------------------
  function renderHeader() {
    const m = data.metrics, d = m.dataset, s = m.split;
    $("subtitle").textContent =
      d.sessions.toLocaleString() + " sessions · " + d.stations + " stations · " +
      d.hours.toLocaleString() + " hourly rows · tested on " + s.test_rows +
      " held-out hours (" + s.test_start.slice(0, 10) + " to " + s.test_end.slice(0, 10) + ")";
  }

  function renderCards() {
    const wrap = $("cards");
    const order = [["baseline", ""], ["linear", "linear"], ["rf_fixed", ""], ["rf", "final"]];
    for (const [key, cls] of order) {
      const mod = data.metrics.models[key];
      const card = el("div", "card " + cls);
      card.appendChild(el("div", "name", mod.name));
      const big = el("div", "big", fmt(mod.mae));
      big.appendChild(el("small", "", " kWh/h MAE"));
      card.appendChild(big);
      card.appendChild(el("div", "rmse", "RMSE " + fmt(mod.rmse) + " kWh/h"));
      if (key === "baseline") {
        card.appendChild(el("div", "rmse", "reference rule"));
      } else {
        const gain = mod.mae_improvement_pct;
        card.appendChild(el("div", "gain" + (gain < 0 ? " neg" : ""),
          pct(gain) + " vs baseline"));
      }
      wrap.appendChild(card);
    }
  }

  // ---- forecast vs actual ---------------------------------------------------
  function windowSize() {
    const v = $("windowSel").value;
    return v === "all" ? data.predictions.hour.length : parseInt(v, 10);
  }

  function drawForecast() {
    const p = data.predictions;
    const n = p.hour.length;
    const size = Math.min(windowSize(), n);
    const slider = $("windowPos");
    slider.max = Math.max(0, n - size);
    if (pendingStart !== null) { slider.value = pendingStart; pendingStart = null; }
    slider.disabled = size >= n;
    const start = Math.min(parseInt(slider.value, 10), n - size);
    const end = start + size;

    $("windowLabel").textContent = p.hour[start] + "  →  " + p.hour[end - 1];

    const cut = (arr) => arr.slice(start, end);
    const sets = [
      { label: "Actual", data: cut(p.actual), borderColor: css("--grey"), borderWidth: 2 },
      { label: "Baseline (same hour last week)", data: cut(p.baseline),
        borderColor: css("--blue"), borderWidth: 1.2, borderDash: [4, 4], hidden: true },
      { label: "Random Forest forecast", data: cut(p.rf), borderColor: css("--green"), borderWidth: 2 },
    ].map((s) => Object.assign({ pointRadius: 0, tension: 0.15 }, s));

    if (forecastChart) {
      forecastChart.data.labels = cut(p.hour);
      sets.forEach((s, i) => {
        s.hidden = forecastChart.data.datasets[i] ? !forecastChart.isDatasetVisible(i) : s.hidden;
      });
      forecastChart.data.datasets = sets;
      forecastChart.update("none");
      return;
    }

    forecastChart = new Chart($("forecastChart"), {
      type: "line",
      data: { labels: cut(p.hour), datasets: sets },
      options: baseOptions("kWh per hour", {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { ticks: { maxTicksLimit: 8, autoSkip: true } } },
      }),
    });
  }

  function baseOptions(yTitle, extra) {
    const grid = css("--line"), ink = css("--muted");
    const opts = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      color: ink,
      scales: {
        x: { grid: { color: grid }, ticks: { color: ink } },
        y: { beginAtZero: true, grid: { color: grid }, ticks: { color: ink },
             title: { display: !!yTitle, text: yTitle, color: ink } },
      },
      plugins: { legend: { labels: { color: ink } } },
    };
    // shallow-merge the nested pieces we override
    for (const key of Object.keys(extra || {})) {
      if (key === "scales") {
        for (const axis of Object.keys(extra.scales)) {
          opts.scales[axis] = Object.assign({}, opts.scales[axis], extra.scales[axis]);
        }
      } else if (key === "plugins") {
        opts.plugins = Object.assign({}, opts.plugins, extra.plugins);
      } else {
        opts[key] = extra[key];
      }
    }
    return opts;
  }

  // ---- error by hour + importance ------------------------------------------
  function renderHourChart() {
    const rows = data.metrics.error_breakdown.by_hour_of_day;
    new Chart($("hourChart"), {
      type: "bar",
      data: {
        labels: rows.map((r) => r.hour_of_day + ":00"),
        datasets: [
          { label: "Baseline", data: rows.map((r) => r.baseline_mae), backgroundColor: css("--grey") },
          { label: "Random Forest", data: rows.map((r) => r.rf_mae), backgroundColor: css("--green") },
        ],
      },
      options: baseOptions("MAE (kWh/h)", {
        plugins: { legend: { position: "bottom" } },
        scales: { x: { ticks: { maxTicksLimit: 12 } } },
      }),
    });
  }

  function renderImportanceChart() {
    const rows = data.metrics.feature_importance;
    new Chart($("importanceChart"), {
      type: "bar",
      data: {
        labels: rows.map((r) => r.feature),
        datasets: [{ label: "Importance", data: rows.map((r) => r.importance),
                     backgroundColor: css("--blue") }],
      },
      options: baseOptions("", {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
      }),
    });
  }

  function renderAblation() {
    const body = document.querySelector("#ablationTable tbody");
    for (const r of data.metrics.ablation) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", "", r.features));
      tr.appendChild(el("td", "num", fmt(r.mae)));
      tr.appendChild(el("td", "num " + (r.mae_improvement_pct >= 0 ? "good" : "bad"),
        pct(r.mae_improvement_pct)));
      body.appendChild(tr);
    }
  }

  // ---- stations -------------------------------------------------------------
  function renderStations() {
    const body = document.querySelector("#stationTable tbody");
    for (const r of data.stations) {
      const tr = document.createElement("tr");
      const gainCell = el("td", "num " + (r.rf_improvement_pct >= 0 ? "good" : "bad"),
        pct(r.rf_improvement_pct));
      tr.appendChild(el("td", "", r.station_id));
      tr.appendChild(el("td", "num", fmt(r.zero_share * 100, 1) + "%"));
      tr.appendChild(el("td", "num", fmt(r.baseline_mae)));
      tr.appendChild(el("td", "num", fmt(r.rf_mae)));
      tr.appendChild(gainCell);
      body.appendChild(tr);
    }
    const st = data.stats;
    $("stationNote").textContent =
      "Across all " + st.stations + " stations the average station has no demand in " +
      fmt(st.station_zero_share_mean * 100, 0) + "% of hours (whole site: " +
      fmt(st.site_zero_share * 100, 0) + "%), which is why the headline model forecasts the " +
      "site total and stations are shown for comparison.";
  }

  // ---- boot -----------------------------------------------------------------
  async function init() {
    try {
      const res = await fetch("/api/data");
      data = await res.json();
      if (!res.ok) { showError(data.error || ("Server returned " + res.status)); return; }
    } catch (err) {
      showError("Could not reach the server: " + err.message);
      return;
    }

    renderHeader();
    renderCards();

    const demo = data.metrics.demo_week;
    pendingStart = demo ? demo.start_index : 0;
    $("windowSel").addEventListener("change", () => { $("windowPos").value = 0; drawForecast(); });
    $("windowPos").addEventListener("input", drawForecast);
    drawForecast();

    renderHourChart();
    renderImportanceChart();
    renderAblation();
    renderStations();
  }

  init();
})();
