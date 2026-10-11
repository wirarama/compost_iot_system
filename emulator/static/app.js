// The device page: polls /api/status and posts the buttons back as JSON.
"use strict";

const POLL_MS = 2000;

const SENSORS = [
  { key: "temperature_c", label: "Temperature", unit: "°C", color: "#F76707", decimals: 1 },
  { key: "moisture_pct", label: "Moisture", unit: "%", color: "#228BE6", decimals: 1 },
  { key: "o2_pct", label: "Oxygen", unit: "%", color: "#12B886", decimals: 1 },
  { key: "co2_pct", label: "CO₂", unit: "%", color: "#7048E8", decimals: 1 },
  { key: "nh3_relative", label: "Ammonia", unit: "rel.", color: "#E64980", decimals: 2 },
];

const UPLINK = {
  unpaired: ["Not paired", "dim", "Pair the device to start sending readings."],
  other_cloud: ["Stopped", "bad", "Paired with a different cloud, see above."],
  paused: ["Paused", "warn", "Uploads are paused. Readings are kept until you resume."],
  idle: ["Waiting", "dim", "Waiting for the next reading."],
  ok: ["Online", "ok", null],
  offline: ["Offline", "warn", null],
  awaiting_setup: ["Awaiting setup", "warn", null],
  error: ["Cloud error", "bad", null],
  rejected: ["Rejected", "bad", null],
  revoked: ["Unpaired", "bad", null],
};

const $ = (id) => document.getElementById(id);
let status = null;

// ------------------------------------------------------------- helpers ---

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function ago(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") node.className = value;
    else node.setAttribute(name, value);
  }
  node.append(...children);
  return node;
}

let toastTimer;
function toast(text) {
  const node = $("toast");
  node.textContent = text;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2600);
}

function setPill(node, label, tone) {
  node.textContent = label;
  node.dataset.tone = tone;
}

// ----------------------------------------------------------- rendering ---

function buildTiles() {
  const tiles = $("tiles");
  for (const sensor of SENSORS) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 30");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    svg.append(document.createElementNS("http://www.w3.org/2000/svg", "polyline"));

    const tile = el("div", { class: "tile", id: `tile-${sensor.key}` },
      el("div", { class: "label" }, sensor.label),
      el("div", {}, el("span", { class: "value" }, "–"), el("span", { class: "unit" }, sensor.unit)),
      el("div", { class: "band" }, ""),
      svg,
    );
    tile.style.setProperty("--accent", sensor.color);
    tiles.append(tile);
  }
}

function renderTiles(s) {
  const expected = s.pile.expected;
  for (const sensor of SENSORS) {
    const tile = $(`tile-${sensor.key}`);
    const value = s.latest ? s.latest[sensor.key] : null;
    tile.querySelector(".value").textContent = value == null ? "–" : value.toFixed(sensor.decimals);

    const [low, high] = expected[sensor.key];
    const band = tile.querySelector(".band");
    const off = value != null && (value < low || value > high);
    band.textContent = `${off ? (value < low ? "Below" : "Above") : "Expected"} ${low}–${high} ${sensor.unit}`;
    band.classList.toggle("off", off);

    const values = s.history.map((reading) => reading[sensor.key]);
    const polyline = tile.querySelector("polyline");
    if (values.length < 2) {
      polyline.setAttribute("points", "");
      continue;
    }
    const min = Math.min(...values, low);
    const max = Math.max(...values, high);
    const span = max - min || 1;
    const points = values.map((v, i) =>
      `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 26}`);
    polyline.setAttribute("points", points.join(" "));
  }
  $("last-reading").textContent = s.latest ? `Last reading ${ago(s.latest.timestamp)}` : "";
}

function renderStage(pile) {
  const color = `var(--stage-${pile.stage_id})`;
  const chip = $("stage-chip");
  chip.textContent = pile.stage_name;
  chip.style.setProperty("--stage", color);

  $("stage-detail").textContent = pile.cured
    ? `Cured · batch day ${pile.batch_day.toFixed(1)} · ready to use, or add feedstock for a new batch`
    : `Day ${pile.stage_day.toFixed(1)} of ~${pile.stage_length_days.toFixed(1)} · batch day ${pile.batch_day.toFixed(1)}`;

  const bar = $("stage-bar");
  if (!bar.children.length) {
    pile.stages.forEach((name, i) => {
      const segment = el("span", { title: name }, el("i"));
      segment.style.setProperty("--stage", `var(--stage-${i})`);
      bar.append(segment);
    });
  }
  [...bar.children].forEach((segment, i) => {
    segment.className = i < pile.stage_id ? "done" : i === pile.stage_id ? "now" : "";
    const share = i === pile.stage_id ? Math.min(1, pile.stage_day / pile.stage_length_days) : 0;
    segment.firstChild.style.width = `${share * 100}%`;
  });
}

function renderPairing(s) {
  const p = s.pairing;
  $("pair-unpaired").hidden = p.state !== "unpaired";
  $("pair-paired").hidden = p.state !== "paired";
  $("pair-other").hidden = p.state !== "other_cloud";

  const revoked = p.state === "unpaired" && s.cloud.state === "revoked";
  $("pair-notice").hidden = !revoked;
  $("pair-notice").textContent = revoked
    ? "This device was unpaired: the cloud stopped accepting its key, most likely because it was " +
      "removed in the dashboard. Get a new code to pair it again."
    : "";
  $("uid-unpaired").textContent = s.device.uid;
  $("pair-url").textContent = s.cloud.pairing_url;
  $("setup-notice").hidden = !(p.state === "paired" && s.cloud.state === "awaiting_setup");
  $("uid-setup").textContent = s.device.uid;
  if (p.state === "paired") {
    $("device-id").textContent = p.device_id || "(not given by the cloud)";
    $("uid-paired").textContent = s.device.uid;
    $("paired-at").textContent = new Date(p.paired_at).toLocaleString();
    $("key-hint").textContent = p.api_key_hint;
  }
  if (p.state === "other_cloud") {
    $("other-cloud").textContent = p.cloud_api_url;
    $("this-cloud").textContent = s.cloud.api_url;
  }
}

function uplinkKey(s) {
  if (s.pairing.state !== "paired") return s.cloud.state === "revoked" ? "revoked" : s.pairing.state;
  if (s.cloud.paused) return "paused";
  return s.cloud.state;
}

function renderUplink(s) {
  const c = s.cloud;
  const [label, tone, fallback] = UPLINK[uplinkKey(s)] || [c.state, "dim", null];
  setPill($("uplink-pill"), label, tone);
  $("uplink-message").textContent = (uplinkKey(s) === c.state && c.message) || fallback || c.message || "";
  $("records-url").textContent = c.records_url;
  $("last-sent").textContent = ago(c.last_success_at);
  $("queued").textContent = c.queued;
  $("sent").textContent = c.sent_total;
  $("dropped").textContent = c.dropped_total;
  $("pause-button").textContent = c.paused ? "Resume uploads" : "Pause uploads";
  $("retry-button").textContent = c.state === "awaiting_setup" ? "Check now" : "Retry now";
  $("retry-button").hidden = !(c.retry_in_seconds > 0 && s.pairing.state === "paired" && !c.paused);

  // header pill sums the device up in one word
  const headline = {
    unpaired: ["Not paired", "dim"], other_cloud: ["Needs reset", "bad"], revoked: ["Unpaired", "bad"],
    paused: ["Paused", "warn"], ok: ["Online", "ok"], idle: ["Paired", "ok"],
    awaiting_setup: ["Finish setup", "warn"],
  }[uplinkKey(s)] || [label, tone];
  setPill($("pill"), ...headline);
}

function renderLog(events) {
  $("log").replaceChildren(...events.map((event) =>
    el("li", {}, el("time", { datetime: event.at }, clock(event.at)), el("span", {}, event.text))));
}

function render(s) {
  status = s;
  $("model").textContent = `${s.device.model} · firmware ${s.device.firmware_version}`;
  renderPairing(s);
  renderTiles(s);
  renderStage(s.pile);
  renderUplink(s);
  renderLog(s.events);
  $("footer").textContent =
    `Hardware ID ${s.device.uid} · a reading every ${s.sampling.reading_interval_seconds} s · ` +
    `compost time runs ${s.sampling.time_scale}× faster than real time`;
}

// -------------------------------------------------------------- events ---

async function poll() {
  try {
    render(await api("/api/status"));
  } catch {
    setPill($("pill"), "Device unreachable", "bad");
  }
}

async function press(button, work) {
  button.disabled = true;
  try {
    await work();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

$("pair-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("code");
  const code = input.value.replace(/\s/g, "");
  $("pair-error").textContent = "";
  press($("pair-button"), async () => {
    $("pair-button").textContent = "Pairing…";
    try {
      render(await api("/api/pair", { code }));
      input.value = "";
      toast("Paired");
    } catch (error) {
      $("pair-error").textContent = error.message;
      input.focus();
    } finally {
      $("pair-button").textContent = "Pair device";
    }
  });
});

$("code").addEventListener("input", (event) => {
  event.target.value = event.target.value.replace(/[^0-9]/g, "").slice(0, 6);
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => press(button, async () => {
    render(await api(`/api/actions/${button.dataset.action}`, {}));
    toast(button.querySelector("b").textContent + ": done");
  }));
});

document.querySelectorAll("[data-reset]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!confirm("Factory reset? This clears the pairing and the API key. The pile keeps going.")) return;
    press(button, async () => {
      render(await api("/api/reset", {}));
      toast("Pairing cleared");
    });
  });
});

$("pause-button").addEventListener("click", (event) => press(event.currentTarget, async () => {
  render(await api("/api/uploads", { paused: !status.cloud.paused }));
}));

$("retry-button").addEventListener("click", (event) => press(event.currentTarget, async () => {
  render(await api("/api/uploads/retry", {}));
}));

buildTiles();
poll();
setInterval(poll, POLL_MS);
