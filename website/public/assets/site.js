// CompostIQ public website. Plain JavaScript, no libraries, no build step.
//
//  1. Live figures: the stat tiles, the two 30-day charts and the country list
//     on the home and open data pages. They come from the API's public
//     endpoints, which NGINX passes through at /api/public/. Until those
//     endpoints exist, or while the API is down, the pages show the preview
//     snapshot in /assets/preview.json and say so.
//  2. Code samples: cURL/Python/R tabs and a Copy button.
//  3. Local preview: dashboard links point at the dashboard on this machine.
//
// Everything else on the site is plain HTML and works without this file.

const DASHBOARD = "https://dashboard.compostiq.win";
const LOCAL_DASHBOARD = "http://127.0.0.1:8050";

// ------------------------------------------------------------ live figures --

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

async function loadFigures() {
  try {
    const [stats, binsByCountry, trends] = await Promise.all([
      getJSON("/api/public/stats"),
      getJSON("/api/public/bins-by-country"),
      getJSON("/api/public/trends"),
    ]);
    return { stats, bins_by_country: binsByCountry, trends, preview: false };
  } catch {
    return { ...(await getJSON("/assets/preview.json")), preview: true };
  }
}

// Dates read "18 Aug", the same in every browser (built-in locales disagree on "Sep"/"Sept").
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (iso) => {
  const d = new Date(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
};
const fullDate = (iso) => `${dayLabel(iso)} ${new Date(iso).getUTCFullYear()}`;
const number = (value) => Number(value).toLocaleString("en");
const short = (value) => String(Math.round(value * 10) / 10);

// A daily line chart as SVG: gridlines, an optional target band or reference
// line, the line itself, and a hover tooltip (the browser's own) per day.
function lineChart(days, values, { series, unit, pointUnit, min, max, ticks, label, threshold, band }) {
  const W = 600, H = 180;
  const x = (i) => (i * W) / Math.max(days.length - 1, 1);
  const y = (v) => H - ((v - min) / (max - min)) * H;
  const f = (n) => n.toFixed(1);
  let svg = `<g transform="translate(44,14)">`;
  if (band) {
    const [low, high, text] = band;
    svg += `<rect class="chart__band" x="0" y="${f(y(high))}" width="${W}" height="${f(y(low) - y(high))}"/>`;
    svg += `<text class="chart__band-label" x="${W}" y="${f(y(high) - 6)}" text-anchor="end">${text}</text>`;
  }
  for (const tick of ticks) {
    svg += `<line class="chart__grid" x1="0" y1="${f(y(tick))}" x2="${W}" y2="${f(y(tick))}"/>`;
    svg += `<text x="-10" y="${f(y(tick) + 4)}" text-anchor="end">${tick}${unit}</text>`;
  }
  svg += `<line class="chart__axis" x1="0" y1="${H}" x2="${W}" y2="${H}"/>`;
  if (threshold) {
    const [value, text] = threshold;
    svg += `<line class="chart__threshold" x1="0" y1="${f(y(value))}" x2="${W}" y2="${f(y(value))}"/>`;
    svg += `<text class="chart__threshold-label" x="${W}" y="${f(y(value) - 7)}" text-anchor="end">${text}</text>`;
  }
  // one polyline per unbroken run, so a day with no reading is a gap
  let run = [];
  for (const [i, v] of [...values, null].entries()) {
    if (v == null) {
      if (run.length > 1) svg += `<polyline class="chart__line chart__line--${series}" points="${run.join(" ")}"/>`;
      run = [];
    } else {
      run.push(`${f(x(i))},${f(y(v))}`);
    }
  }
  svg += `<g class="chart__pts chart__pts--${series}">`;
  values.forEach((v, i) => {
    if (v != null) svg += `<circle cx="${f(x(i))}" cy="${f(y(v))}" r="6"><title>${dayLabel(days[i])}: ${short(v)} ${pointUnit}</title></circle>`;
  });
  svg += `</g>`;
  const mid = Math.floor(days.length / 2);
  svg += `<text x="0" y="${H + 24}">${dayLabel(days[0])}</text>`;
  svg += `<text x="${f(x(mid))}" y="${H + 24}" text-anchor="middle">${dayLabel(days[mid])}</text>`;
  svg += `<text x="${W}" y="${H + 24}" text-anchor="end">${dayLabel(days[days.length - 1])}</text>`;
  svg += `</g>`;
  return `<svg class="chart" viewBox="0 0 660 230" role="img" aria-label="${label}">${svg}</svg>`;
}

function drawCharts(trendDays) {
  const days = trendDays.slice(-30);
  if (days.length < 2) return;
  const dates = days.map((d) => d.date);
  const temps = days.map((d) => d.temperature_c);
  const moist = days.map((d) => d.moisture_pct);
  const known = (list) => list.filter((v) => v != null);
  const span = `${dayLabel(dates[0])} to ${dayLabel(dates[dates.length - 1])}`;
  const charts = {
    temperature: lineChart(dates, temps, {
      series: "temp", unit: "°", pointUnit: "°C", min: 20, max: 70, ticks: [30, 50, 70],
      threshold: [55, "55 °C pathogen kill"],
      label: `Daily mean temperature, ${span}. It ranges from ${Math.round(Math.min(...known(temps)))} to ${Math.round(Math.max(...known(temps)))} °C.`,
    }),
    moisture: lineChart(dates, moist, {
      series: "moist", unit: "%", pointUnit: "%", min: 30, max: 70, ticks: [40, 55, 70],
      band: [40, 65, "Target 40–65 %"],
      label: `Daily mean moisture, ${span}. It ranges from ${Math.round(Math.min(...known(moist)))} to ${Math.round(Math.max(...known(moist)))} %, against a 40 to 65 % target.`,
    }),
  };
  document.querySelectorAll("[data-chart]").forEach((slot) => {
    slot.innerHTML = charts[slot.dataset.chart] ?? "";
  });
  document.querySelectorAll("[data-trend-range]").forEach((el) => {
    el.textContent = `${dayLabel(dates[0])} – ${fullDate(dates[dates.length - 1])}`;
  });
}

function listCountries(countries) {
  const most = Math.max(1, ...countries.map((c) => c.bins));
  document.querySelectorAll("[data-countries]").forEach((list) => {
    list.replaceChildren(...countries.map((c) => {
      const item = document.createElement("li");
      item.className = "country";
      const row = document.createElement("div");
      row.className = "country__row";
      const name = document.createElement("span");
      name.textContent = c.name || c.country;
      const count = document.createElement("span");
      count.className = "country__count";
      count.textContent = `${c.bins} ${c.bins === 1 ? "bin" : "bins"}`;
      row.append(name, count);
      item.append(row);
      item.insertAdjacentHTML("beforeend",
        `<svg class="bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">` +
        `<rect class="bar__track" width="100" height="8" rx="4"/>` +
        `<rect class="bar__fill" width="${((100 * c.bins) / most).toFixed(1)}" height="8" rx="4"/></svg>`);
      return item;
    }));
  });
}

async function showFigures() {
  if (!document.querySelector("[data-stat], [data-chart], [data-countries]")) return;
  let figures;
  try {
    figures = await loadFigures();
  } catch {
    return; // neither the API nor the preview answered: the dashes stay
  }
  const { stats } = figures;
  const values = {
    ...stats,
    period: stats.first_reading_at ? `${dayLabel(stats.first_reading_at)} – ${fullDate(stats.last_reading_at)}` : "–",
  };
  document.querySelectorAll("[data-stat]").forEach((el) => {
    const value = values[el.dataset.stat];
    if (value != null) el.textContent = typeof value === "number" ? number(value) : value;
  });
  drawCharts(figures.trends.days ?? figures.trends);
  listCountries(figures.bins_by_country);
  document.querySelectorAll("[data-figures-note]").forEach((note) => {
    note.textContent = figures.preview
      ? "Preview figures from the simulated pilot dataset, until the public API is live."
      : `Updated ${fullDate(stats.updated_at)}, ${new Date(stats.updated_at).toISOString().slice(11, 16)} UTC.`;
  });
}

showFigures();

// ------------------------------------------------------------ code samples --

document.querySelectorAll("[data-tabs]").forEach((box) => {
  const tabs = box.querySelectorAll("[data-tab]");
  const panels = box.querySelectorAll("[data-panel]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.setAttribute("aria-pressed", String(t === tab)));
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.panel !== tab.dataset.tab;
      });
    });
  });
});

if (navigator.clipboard) {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", async () => {
      const code = button.closest(".codebox").querySelector("pre:not([hidden])");
      // Drop the "$ " shell prompt so the command can be pasted as is.
      await navigator.clipboard.writeText(code.innerText.replace(/^\$ /gm, ""));
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy"; }, 1500);
    });
  });
}

// ----------------------------------------------------------- local preview --

if (["localhost", "127.0.0.1"].includes(location.hostname)) {
  document.querySelectorAll(`a[href^="${DASHBOARD}"]`).forEach((link) => {
    link.href = link.href.replace(DASHBOARD, LOCAL_DASHBOARD);
  });
}
