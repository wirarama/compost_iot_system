"""
============================================================
 KOMPOS IoT — Flask API Server
 Menerima data JSON dari ESP8266 via HTTP POST
 Menyimpan ke SQLite, menjalankan SPRT online, menyediakan
 endpoint agregasi untuk Streamlit dashboard
============================================================
 Endpoint:
   POST /api/data          <- ESP8266 kirim data sensor
   GET  /api/latest        <- Data terbaru (1 baris)
   GET  /api/history       <- Riwayat semua data
   GET  /api/aggregate     <- Statistik agregasi per fase/jam
   GET  /api/status        <- Status server + statistik
   GET  /api/health        <- Health check
   DELETE /api/reset       <- Reset database (dev only)
============================================================
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import os
import math
import logging
from datetime import datetime, timedelta
from collections import deque
import numpy as np

# ─── KONFIGURASI ────────────────────────────────────────────
DB_PATH   = "kompos_data.db"
LOG_LEVEL = logging.INFO

# Definisi fase
FASE_DEF = {
    0: {"nama": "Mesophilik Awal",    "warna": "#52B788"},
    1: {"nama": "Termofilik Aktif",   "warna": "#E76F51"},
    2: {"nama": "Puncak Dekomposisi", "warna": "#9B2226"},
    3: {"nama": "Pendinginan",        "warna": "#219EBC"},
    4: {"nama": "Maturasi",           "warna": "#8B5E3C"},
    5: {"nama": "Kompos Matang",      "warna": "#6B7280"},
}

# Parameter SPRT
SPRT_ALPHA = 0.05
SPRT_BETA  = 0.10
SPRT_A     =  math.log((1 - SPRT_BETA) / SPRT_ALPHA)
SPRT_B     =  math.log(SPRT_BETA / (1 - SPRT_ALPHA))

# Threshold Bayesian fusion per fase
FASE_RANGES = {
    0: {"suhu": (18, 40),  "moisture": (60, 85), "gas": (30,  110)},
    1: {"suhu": (38, 68),  "moisture": (42, 72), "gas": (80,  290)},
    2: {"suhu": (50, 72),  "moisture": (32, 56), "gas": (250, 620)},
    3: {"suhu": (26, 60),  "moisture": (36, 56), "gas": (100, 510)},
    4: {"suhu": (23, 37),  "moisture": (36, 55), "gas": (45,  170)},
    5: {"suhu": (18, 30),  "moisture": (33, 52), "gas": (18,   95)},
}

# ─── LOGGING ────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("KomposAPI")

# ─── FLASK APP ───────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# ─── IN-MEMORY SPRT STATE ───────────────────────────────────
# Menyimpan CUSUM per sensor per transisi (persists between requests)
sprt_state = {}

# Hipotesis SPRT: (sensor, mu0, mu1, sigma, target_fase)
SPRT_HYPOTHESES = [
    ("suhu",     22, 35,  5, 0), ("suhu",    35, 55,  8, 1),
    ("suhu",     55, 65,  6, 2), ("suhu",    65, 38,  9, 3),
    ("suhu",     38, 29,  5, 4), ("suhu",    29, 23,  3, 5),
    ("moisture", 68, 58,  6, 0), ("moisture",58, 44,  7, 1),
    ("moisture", 44, 38,  5, 2), ("moisture",38, 44,  5, 3),
    ("moisture", 44, 42,  4, 4), ("moisture",42, 38,  3, 5),
    ("gas",      55,120, 30, 0), ("gas",    120,250, 50, 1),
    ("gas",     250,450, 70, 2), ("gas",    450,200, 80, 3),
    ("gas",     200, 90, 45, 4), ("gas",     90, 50, 22, 5),
]

for (sensor, mu0, mu1, sigma, ftgt) in SPRT_HYPOTHESES:
    sprt_state[f"{sensor}_f{ftgt}"] = {"cusum": 0.0, "detections": 0}

# Smoothing window untuk prediksi fase
phase_history = deque(maxlen=7)


# ════════════════════════════════════════════════════════════
# DATABASE SETUP
# ════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id     TEXT    NOT NULL DEFAULT 'ESP8266_01',
            timestamp     TEXT    NOT NULL,
            suhu          REAL    NOT NULL,
            moisture      REAL    NOT NULL,
            gas           REAL    NOT NULL,
            fase_pred     INTEGER NOT NULL DEFAULT 0,
            fase_nama     TEXT    NOT NULL DEFAULT 'Mesophilik Awal',
            ikk           REAL    NOT NULL DEFAULT 0.0,
            sprt_cusum_t  REAL    DEFAULT 0.0,
            sprt_cusum_m  REAL    DEFAULT 0.0,
            sprt_cusum_g  REAL    DEFAULT 0.0,
            raw_payload   TEXT,
            created_at    TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aggregation_hourly (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            hour_bucket   TEXT    NOT NULL,
            device_id     TEXT    NOT NULL DEFAULT 'ESP8266_01',
            suhu_mean     REAL, suhu_min REAL, suhu_max REAL, suhu_std REAL,
            moisture_mean REAL, moisture_min REAL, moisture_max REAL, moisture_std REAL,
            gas_mean      REAL, gas_min REAL, gas_max REAL, gas_std REAL,
            ikk_mean      REAL, ikk_min REAL, ikk_max REAL,
            fase_mode     INTEGER, sample_count INTEGER,
            updated_at    TEXT    DEFAULT (datetime('now','localtime')),
            UNIQUE(hour_bucket, device_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp ON sensor_data(timestamp);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_device ON sensor_data(device_id);
    """)
    conn.commit()
    conn.close()
    log.info(f"Database initialized: {DB_PATH}")


# ════════════════════════════════════════════════════════════
# SPRT + BAYESIAN FUSION ENGINE
# ════════════════════════════════════════════════════════════
def sprt_update(key, val, mu0, mu1, sigma):
    """Update SPRT CUSUM untuk satu hipotesis, return (cusum, detected)."""
    state = sprt_state[key]
    # Log-likelihood ratio inkremental
    llr = ((val - mu0) * (mu1 - mu0) / sigma**2
           - (mu1 - mu0)**2 / (2 * sigma**2))
    state["cusum"] += llr

    detected = 0
    if state["cusum"] >= SPRT_A:
        detected = 1
        state["cusum"] = 0.0
        state["detections"] += 1
    elif state["cusum"] <= SPRT_B:
        detected = -1
        state["cusum"] = 0.0
    return state["cusum"], detected


def soft_membership(val, lo, hi, margin=8.0):
    """Soft membership function — 1.0 jika dalam range, linear decay di luar."""
    if lo <= val <= hi:
        return 1.0
    elif val < lo:
        return max(0.0, 1.0 - (lo - val) / margin)
    else:
        return max(0.0, 1.0 - (val - hi) / margin)


def detect_phase(suhu, moisture, gas):
    """
    Dua-lapis deteksi fase:
    Lapis 1: SPRT → skor prior per fase
    Lapis 2: Bayesian likelihood fusion
    Kembalikan (fase_pred, fase_nama, sprt_cusums)
    """
    scores = [0.3] * 6
    cusum_t = 0.0
    cusum_m = 0.0
    cusum_g = 0.0

    for (sensor, mu0, mu1, sigma, ftgt) in SPRT_HYPOTHESES:
        key = f"{sensor}_f{ftgt}"
        val = {"suhu": suhu, "moisture": moisture, "gas": gas}[sensor]
        cusum, _ = sprt_update(key, val, mu0, mu1, sigma)
        prob = 1.0 / (1.0 + math.exp(-cusum / max(abs(SPRT_A), 0.1)))
        scores[ftgt] += prob * 1.2

        # Track cusum per sensor type (ambil representative)
        if sensor == "suhu"     and ftgt == 1: cusum_t = cusum
        if sensor == "moisture" and ftgt == 1: cusum_m = cusum
        if sensor == "gas"      and ftgt == 2: cusum_g = cusum

    # Bayesian fusion dengan likelihood per fase
    fused = [0.0] * 6
    for f in range(6):
        r = FASE_RANGES[f]
        lk = (soft_membership(suhu,     *r["suhu"],     8) *
              soft_membership(moisture, *r["moisture"], 10) *
              soft_membership(gas,      *r["gas"],      40))
        prior = scores[f] / sum(scores)
        fused[f] = prior * (lk + 0.05)

    s = sum(fused)
    if s > 0:
        fused = [x / s for x in fused]

    fase_pred = fused.index(max(fused))

    # Majority vote smoothing
    phase_history.append(fase_pred)
    from collections import Counter
    fase_smooth = Counter(phase_history).most_common(1)[0][0]

    return fase_smooth, FASE_DEF[fase_smooth]["nama"], cusum_t, cusum_m, cusum_g


def compute_ikk(suhu, moisture, gas, fase):
    """Hitung Indeks Kesehatan Kompos 0-100."""
    r = FASE_RANGES[fase]

    def sc(val, lo, hi):
        span = hi - lo
        if lo <= val <= hi: return 1.0
        elif val < lo: return max(0.0, 1.0 - (lo - val) / (span * 0.25 + 1))
        else:          return max(0.0, 1.0 - (val - hi) / (span * 0.25 + 1))

    s_t = sc(suhu,     *r["suhu"])
    s_m = sc(moisture, *r["moisture"])
    s_g = sc(gas,      *r["gas"])
    return round((0.40 * s_t + 0.35 * s_m + 0.25 * s_g) * 100, 2)


# ════════════════════════════════════════════════════════════
# AGREGASI HOURLY
# ════════════════════════════════════════════════════════════
def update_hourly_aggregation(device_id, hour_bucket):
    """Hitung dan simpan agregasi per jam setelah setiap insert."""
    conn = get_db()
    rows = conn.execute("""
        SELECT suhu, moisture, gas, ikk, fase_pred
        FROM sensor_data
        WHERE device_id = ? AND strftime('%Y-%m-%d %H', timestamp) = ?
    """, (device_id, hour_bucket)).fetchall()

    if not rows:
        conn.close()
        return

    suhu_vals     = [r["suhu"]     for r in rows]
    moisture_vals = [r["moisture"] for r in rows]
    gas_vals      = [r["gas"]      for r in rows]
    ikk_vals      = [r["ikk"]      for r in rows]
    fase_vals     = [r["fase_pred"] for r in rows]

    def stats(lst):
        n = len(lst)
        mean = sum(lst) / n
        mn   = min(lst)
        mx   = max(lst)
        std  = (sum((x - mean)**2 for x in lst) / n) ** 0.5 if n > 1 else 0
        return round(mean, 3), round(mn, 3), round(mx, 3), round(std, 3)

    from collections import Counter
    fase_mode = Counter(fase_vals).most_common(1)[0][0]

    sm, sn, sx, ss = stats(suhu_vals)
    mm, mn_m, mx_m, ms = stats(moisture_vals)
    gm, gn, gx, gs = stats(gas_vals)
    im, in_i, ix_i, _ = stats(ikk_vals)

    conn.execute("""
        INSERT INTO aggregation_hourly
            (hour_bucket, device_id,
             suhu_mean, suhu_min, suhu_max, suhu_std,
             moisture_mean, moisture_min, moisture_max, moisture_std,
             gas_mean, gas_min, gas_max, gas_std,
             ikk_mean, ikk_min, ikk_max,
             fase_mode, sample_count, updated_at)
        VALUES (?,?,  ?,?,?,?,  ?,?,?,?,  ?,?,?,?,  ?,?,?,  ?,?,  datetime('now','localtime'))
        ON CONFLICT(hour_bucket, device_id) DO UPDATE SET
            suhu_mean=excluded.suhu_mean, suhu_min=excluded.suhu_min,
            suhu_max=excluded.suhu_max, suhu_std=excluded.suhu_std,
            moisture_mean=excluded.moisture_mean, moisture_min=excluded.moisture_min,
            moisture_max=excluded.moisture_max, moisture_std=excluded.moisture_std,
            gas_mean=excluded.gas_mean, gas_min=excluded.gas_min,
            gas_max=excluded.gas_max, gas_std=excluded.gas_std,
            ikk_mean=excluded.ikk_mean, ikk_min=excluded.ikk_min,
            ikk_max=excluded.ikk_max, fase_mode=excluded.fase_mode,
            sample_count=excluded.sample_count,
            updated_at=excluded.updated_at
    """, (hour_bucket, device_id,
          sm, sn, sx, ss, mm, mn_m, mx_m, ms,
          gm, gn, gx, gs, im, in_i, ix_i,
          fase_mode, len(rows)))
    conn.commit()
    conn.close()


# ════════════════════════════════════════════════════════════
# API ENDPOINTS
# ════════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "server": "KomposIoT", "version": "2.0"})


@app.route("/api/data", methods=["POST"])
def receive_data():
    """
    Endpoint utama: menerima data sensor dari ESP8266.

    Expected JSON payload:
    {
        "device_id": "ESP8266_01",
        "timestamp": "2024-03-15T14:30:00",   (opsional, default: now)
        "suhu": 54.3,
        "moisture": 48.2,
        "gas": 185.6
    }
    """
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    payload = request.get_json(force=True)
    log.info(f"Received: {payload}")

    # Validasi field wajib
    required = ["suhu", "moisture", "gas"]
    missing  = [f for f in required if f not in payload]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    # Parse dan validasi nilai
    try:
        suhu     = float(payload["suhu"])
        moisture = float(payload["moisture"])
        gas      = float(payload["gas"])
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid numeric values: {e}"}), 400

    # Validasi range sensor
    if not (0 <= suhu <= 100):
        return jsonify({"error": f"suhu out of range: {suhu}"}), 400
    if not (0 <= moisture <= 100):
        return jsonify({"error": f"moisture out of range: {moisture}"}), 400
    if not (0 <= gas <= 1000):
        return jsonify({"error": f"gas out of range: {gas}"}), 400

    device_id = payload.get("device_id", "ESP8266_01")
    timestamp = payload.get("timestamp",
                            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    # Deteksi fase + IKK
    fase_pred, fase_nama, ct, cm, cg = detect_phase(suhu, moisture, gas)
    ikk = compute_ikk(suhu, moisture, gas, fase_pred)

    # Simpan ke database
    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO sensor_data
            (device_id, timestamp, suhu, moisture, gas,
             fase_pred, fase_nama, ikk,
             sprt_cusum_t, sprt_cusum_m, sprt_cusum_g,
             raw_payload)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (device_id, timestamp, suhu, moisture, gas,
          fase_pred, fase_nama, ikk,
          round(ct, 4), round(cm, 4), round(cg, 4),
          json.dumps(payload)))
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()

    # Update agregasi hourly
    hour_bucket = timestamp[:13]  # "2024-03-15T14" → "2024-03-15 14"
    hour_bucket = hour_bucket.replace("T", " ")
    update_hourly_aggregation(device_id, hour_bucket)

    # Rekomendasi otomatis
    alerts = []
    if moisture < 38:  alerts.append("MOISTURE_LOW")
    if moisture > 72:  alerts.append("MOISTURE_HIGH")
    if gas > 500:      alerts.append("GAS_CRITICAL")
    if suhu > 70:      alerts.append("TEMP_CRITICAL")
    if ikk < 40:       alerts.append("IKK_CRITICAL")

    response = {
        "id":        row_id,
        "status":    "ok",
        "timestamp": timestamp,
        "device_id": device_id,
        "sensors":   {"suhu": suhu, "moisture": moisture, "gas": gas},
        "analysis":  {
            "fase_pred": fase_pred,
            "fase_nama": fase_nama,
            "ikk":       ikk,
            "sprt": {"cusum_t": round(ct,3), "cusum_m": round(cm,3), "cusum_g": round(cg,3)},
        },
        "alerts": alerts,
    }
    log.info(f"→ Fase={fase_nama} IKK={ikk} Alerts={alerts}")
    return jsonify(response), 201


@app.route("/api/latest", methods=["GET"])
def get_latest():
    """Data terbaru dari setiap device."""
    device_id = request.args.get("device_id", "ESP8266_01")
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM sensor_data
        WHERE device_id = ?
        ORDER BY id DESC LIMIT 1
    """, (device_id,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "No data yet"}), 404

    return jsonify(dict(row))


@app.route("/api/history", methods=["GET"])
def get_history():
    """
    Riwayat data sensor.
    Query params:
      device_id  (default: ESP8266_01)
      limit      (default: 500)
      hours      (default: 24, ambil N jam terakhir)
    """
    device_id = request.args.get("device_id", "ESP8266_01")
    limit     = min(int(request.args.get("limit", 500)), 5000)
    hours     = int(request.args.get("hours", 24))
    since     = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")

    conn = get_db()
    rows = conn.execute("""
        SELECT id, device_id, timestamp, suhu, moisture, gas,
               fase_pred, fase_nama, ikk,
               sprt_cusum_t, sprt_cusum_m, sprt_cusum_g
        FROM sensor_data
        WHERE device_id = ? AND timestamp >= ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (device_id, since, limit)).fetchall()
    conn.close()

    return jsonify({
        "device_id": device_id,
        "count":     len(rows),
        "hours":     hours,
        "data":      [dict(r) for r in rows],
    })


@app.route("/api/aggregate", methods=["GET"])
def get_aggregate():
    """
    Data agregasi multi-level.
    Query params:
      device_id  (default: ESP8266_01)
      level      hourly | daily | fase  (default: hourly)
      days       (default: 7)
    """
    device_id = request.args.get("device_id", "ESP8266_01")
    level     = request.args.get("level", "hourly")
    days      = int(request.args.get("days", 7))
    since     = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    conn = get_db()

    if level == "hourly":
        rows = conn.execute("""
            SELECT hour_bucket, suhu_mean, suhu_min, suhu_max, suhu_std,
                   moisture_mean, moisture_min, moisture_max, moisture_std,
                   gas_mean, gas_min, gas_max, gas_std,
                   ikk_mean, ikk_min, ikk_max,
                   fase_mode, sample_count
            FROM aggregation_hourly
            WHERE device_id = ? AND hour_bucket >= ?
            ORDER BY hour_bucket ASC
        """, (device_id, since[:13])).fetchall()

    elif level == "daily":
        rows = conn.execute("""
            SELECT date(timestamp) as day,
                   avg(suhu) as suhu_mean, min(suhu) as suhu_min, max(suhu) as suhu_max,
                   avg(moisture) as moisture_mean,
                   avg(gas) as gas_mean, max(gas) as gas_max,
                   avg(ikk) as ikk_mean, min(ikk) as ikk_min,
                   count(*) as sample_count
            FROM sensor_data
            WHERE device_id = ? AND timestamp >= ?
            GROUP BY date(timestamp)
            ORDER BY day ASC
        """, (device_id, since)).fetchall()

    elif level == "fase":
        rows = conn.execute("""
            SELECT fase_pred, fase_nama,
                   avg(suhu) as suhu_mean, min(suhu) as suhu_min, max(suhu) as suhu_max,
                   avg(moisture) as moisture_mean,
                   avg(gas) as gas_mean, max(gas) as gas_max,
                   avg(ikk) as ikk_mean,
                   count(*) as sample_count,
                   min(timestamp) as first_seen,
                   max(timestamp) as last_seen
            FROM sensor_data
            WHERE device_id = ? AND timestamp >= ?
            GROUP BY fase_pred
            ORDER BY fase_pred ASC
        """, (device_id, since)).fetchall()

    else:
        conn.close()
        return jsonify({"error": f"Unknown level: {level}"}), 400

    conn.close()
    return jsonify({
        "device_id": device_id,
        "level":     level,
        "days":      days,
        "count":     len(rows),
        "data":      [dict(r) for r in rows],
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    """Status server + statistik database."""
    conn = get_db()
    total = conn.execute("SELECT count(*) as n FROM sensor_data").fetchone()["n"]
    devices = conn.execute(
        "SELECT DISTINCT device_id FROM sensor_data"
    ).fetchall()
    latest = conn.execute("""
        SELECT device_id, timestamp, suhu, moisture, gas, fase_nama, ikk
        FROM sensor_data ORDER BY id DESC LIMIT 1
    """).fetchone()
    conn.close()

    sprt_info = {
        k: {"cusum": round(v["cusum"], 3), "detections": v["detections"]}
        for k, v in sprt_state.items()
    }

    return jsonify({
        "server":    "KomposIoT API v2.0",
        "db_path":   os.path.abspath(DB_PATH),
        "total_records": total,
        "devices":   [r["device_id"] for r in devices],
        "latest":    dict(latest) if latest else None,
        "sprt_state": sprt_info,
        "sprt_params": {"A": round(SPRT_A, 3), "B": round(SPRT_B, 3),
                        "alpha": SPRT_ALPHA, "beta": SPRT_BETA},
    })


@app.route("/api/reset", methods=["DELETE"])
def reset_db():
    """Reset database — hanya untuk development."""
    conn = get_db()
    conn.execute("DELETE FROM sensor_data")
    conn.execute("DELETE FROM aggregation_hourly")
    conn.commit()
    conn.close()

    # Reset SPRT state
    for key in sprt_state:
        sprt_state[key] = {"cusum": 0.0, "detections": 0}
    phase_history.clear()

    log.warning("Database reset by API call")
    return jsonify({"status": "ok", "message": "Database reset"})


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    log.info("="*55)
    log.info("  KomposIoT API Server v2.0")
    log.info("  POST /api/data    <- ESP8266 kirim sensor")
    log.info("  GET  /api/latest  <- Data terbaru")
    log.info("  GET  /api/history <- Riwayat data")
    log.info("  GET  /api/aggregate?level=hourly|daily|fase")
    log.info("  GET  /api/status  <- Status + SPRT state")
    log.info("="*55)
    app.run(host="0.0.0.0", port=5000, debug=False)
