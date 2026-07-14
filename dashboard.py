"""
============================================================
 KOMPOS IoT — Streamlit Dashboard v2
 Real-time monitoring + Agregasi + Analisis SPRT
============================================================
 Jalankan: streamlit run dashboard.py
 Pastikan server.py berjalan di port 5000
============================================================
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import requests
import json
from datetime import datetime, timedelta
import time

# ─── KONFIGURASI ────────────────────────────────────────────
st.set_page_config(
    page_title="KomposIoT Monitor",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = "http://localhost:5000/api"

# Definisi fase
FASE_DEF = {
    0: {"nama": "Mesophilik Awal",    "warna": "#52B788", "emoji": "🌱"},
    1: {"nama": "Termofilik Aktif",   "warna": "#E76F51", "emoji": "🔥"},
    2: {"nama": "Puncak Dekomposisi", "warna": "#9B2226", "emoji": "⚡"},
    3: {"nama": "Pendinginan",        "warna": "#219EBC", "emoji": "❄️"},
    4: {"nama": "Maturasi",           "warna": "#8B5E3C", "emoji": "🌾"},
    5: {"nama": "Kompos Matang",      "warna": "#6B7280", "emoji": "✅"},
}

SENSOR_CFG = {
    "suhu":     {"label": "Suhu (°C)",           "color": "#FF8C42", "icon": "🌡️",
                 "unit": "°C",  "min": 0, "max": 80},
    "moisture": {"label": "Kelembapan (%)",       "color": "#38BDF8", "icon": "💧",
                 "unit": "%",   "min": 0, "max": 100},
    "gas":      {"label": "Gas MQ-135 (ppm)",     "color": "#86EFAC", "icon": "🌫️",
                 "unit": "ppm", "min": 0, "max": 700},
}

# ─── HELPER FUNGSI ───────────────────────────────────────────
@st.cache_data(ttl=5)
def fetch_latest(device_id="ESP8266_01"):
    try:
        r = requests.get(f"{API_BASE}/latest",
                         params={"device_id": device_id}, timeout=3)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


@st.cache_data(ttl=10)
def fetch_history(device_id="ESP8266_01", hours=24, limit=500):
    try:
        r = requests.get(f"{API_BASE}/history",
                         params={"device_id": device_id, "hours": hours, "limit": limit},
                         timeout=5)
        if r.status_code == 200:
            data = r.json()
            df = pd.DataFrame(data["data"])
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def fetch_aggregate(device_id="ESP8266_01", level="hourly", days=7):
    try:
        r = requests.get(f"{API_BASE}/aggregate",
                         params={"device_id": device_id, "level": level, "days": days},
                         timeout=5)
        if r.status_code == 200:
            return pd.DataFrame(r.json()["data"])
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=15)
def fetch_status():
    try:
        r = requests.get(f"{API_BASE}/status", timeout=3)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def ikk_color(val):
    if val >= 80: return "#22C55E"
    if val >= 60: return "#F59E0B"
    if val >= 40: return "#EF4444"
    return "#7F1D1D"


def ikk_label(val):
    if val >= 80: return "Optimal 🟢"
    if val >= 60: return "Baik 🟡"
    if val >= 40: return "Perlu Perhatian 🟠"
    return "Kritis 🔴"


def hex_rgba(hex6, alpha=255):
    """Convert '#RRGGBB' + alpha byte (0-255) to an 'rgba(r,g,b,a)' string.
    Plotly rejects 8-digit #RRGGBBAA hex, so opacity tints must use rgba()."""
    h = hex6.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{round(alpha / 255, 3)})"


def send_test_data(suhu, moisture, gas, device_id):
    payload = {
        "device_id": device_id,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "suhu":      suhu,
        "moisture":  moisture,
        "gas":       gas,
    }
    try:
        r = requests.post(f"{API_BASE}/data",
                          json=payload, timeout=5)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}


# ─── CUSTOM CSS ──────────────────────────────────────────────
st.markdown("""
<style>
/* Main theme */
.stApp { background-color: #0F1923; color: #D8E4F0; }
section[data-testid="stSidebar"] { background: #162232; }
section[data-testid="stSidebar"] * { color: #D8E4F0 !important; }

/* Metric cards */
.metric-card {
    background: #162232;
    border-radius: 10px;
    padding: 16px 20px;
    border-left: 4px solid;
    margin-bottom: 8px;
}
.metric-value { font-size: 2.2rem; font-weight: 700; line-height: 1.1; }
.metric-label { font-size: 0.8rem; opacity: 0.7; margin-top: 2px; }
.metric-delta { font-size: 0.85rem; margin-top: 6px; }

/* Fase badge */
.fase-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.85rem;
    color: white;
    margin: 4px 0;
}

/* Alert box */
.alert-box {
    border-radius: 8px;
    padding: 10px 14px;
    margin: 4px 0;
    font-size: 0.85rem;
    font-weight: 500;
}
.alert-critical { background: #7F1D1D; border-left: 4px solid #EF4444; }
.alert-warning  { background: #451A03; border-left: 4px solid #F59E0B; }
.alert-ok       { background: #14532D; border-left: 4px solid #22C55E; }

/* Tab styling */
.stTabs [data-baseweb="tab-list"] { background: #162232; border-radius: 8px; }
.stTabs [data-baseweb="tab"] { color: #D8E4F0 !important; }

/* Plotly chart background override */
.js-plotly-plot .plotly { background: transparent !important; }

/* Section divider */
.section-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: #38BDF8;
    border-bottom: 1px solid #1E3048;
    padding-bottom: 6px;
    margin: 16px 0 10px 0;
}
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🌿 KomposIoT")
    st.markdown("**Monitoring Kompos Berbasis IoT**")
    st.markdown("DS18B20 · Soil Moisture · MQ-135")
    st.divider()

    device_id = st.text_input("Device ID", value="ESP8266_01")
    auto_refresh = st.toggle("Auto Refresh (10 dtk)", value=False)
    refresh_interval = st.slider("Interval (detik)", 5, 60, 10, step=5,
                                  disabled=not auto_refresh)

    st.divider()
    st.markdown("### 📡 Kirim Data Manual")
    st.caption("Simulasi pengiriman ESP8266")
    with st.form("manual_send"):
        m_suhu     = st.number_input("Suhu (°C)", 10.0, 80.0, 35.0, 0.1)
        m_moisture = st.number_input("Kelembapan (%)", 10.0, 95.0, 55.0, 0.5)
        m_gas      = st.number_input("Gas (ppm)", 20.0, 700.0, 120.0, 1.0)
        send_btn   = st.form_submit_button("📤 Kirim", use_container_width=True)

    if send_btn:
        code, resp = send_test_data(m_suhu, m_moisture, m_gas, device_id)
        if code == 201:
            st.success(f"✅ Terkirim! Fase: {resp['analysis']['fase_nama']}")
            st.caption(f"IKK: {resp['analysis']['ikk']} | ID: {resp['id']}")
            if resp.get("alerts"):
                for a in resp["alerts"]:
                    st.warning(f"⚠️ {a}")
        else:
            st.error(f"❌ Error {code}: {resp}")

    st.divider()

    # Server status indicator
    status = fetch_status()
    if status:
        st.markdown(f"🟢 **Server Online**")
        st.caption(f"Total records: {status.get('total_records', 0)}")
        if status.get("latest"):
            lts = status["latest"]["timestamp"]
            st.caption(f"Last data: {lts}")
    else:
        st.markdown("🔴 **Server Offline**")
        st.caption("Pastikan server.py berjalan")

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ─── AUTO REFRESH ────────────────────────────────────────────
if auto_refresh:
    time.sleep(refresh_interval)
    st.cache_data.clear()
    st.rerun()

# ════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════
st.markdown("""
<div style="background:linear-gradient(90deg,#162232,#1E3048);
            border-radius:12px; padding:16px 24px; margin-bottom:16px;
            border-left:4px solid #38BDF8;">
  <h1 style="margin:0; color:#D8E4F0; font-size:1.6rem;">
    🌿 Sistem Monitoring Kompos IoT
  </h1>
  <p style="margin:4px 0 0 0; color:#94A3B8; font-size:0.9rem;">
    Sequential Analysis (SPRT-CUSUM) · Deteksi Fase Real-time · Dashboard Agregasi
  </p>
</div>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════
# TABS UTAMA
# ════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Live Monitor",
    "📈 Tren Sensor",
    "🔬 Analisis SPRT",
    "📉 Agregasi",
    "⚙️ Konfigurasi",
])

# ════════════════════════════════════════════════════════════
# TAB 1: LIVE MONITOR
# ════════════════════════════════════════════════════════════
with tab1:
    latest = fetch_latest(device_id)
    history_df = fetch_history(device_id, hours=2, limit=120)

    if latest is None:
        st.info("⏳ Belum ada data. Kirim data dari ESP8266 atau gunakan form manual di sidebar.")
        st.stop()

    # ── Row 1: Metric Cards ──────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        suhu = latest.get("suhu", 0)
        st.markdown(f"""
        <div class="metric-card" style="border-color:#FF8C42">
          <div class="metric-label">🌡️ Suhu DS18B20</div>
          <div class="metric-value" style="color:#FF8C42">{suhu:.1f}°C</div>
          <div class="metric-delta">Range normal: 20–70°C</div>
        </div>""", unsafe_allow_html=True)

    with c2:
        moist = latest.get("moisture", 0)
        m_color = "#22C55E" if 38 <= moist <= 72 else "#EF4444"
        st.markdown(f"""
        <div class="metric-card" style="border-color:#38BDF8">
          <div class="metric-label">💧 Kelembapan</div>
          <div class="metric-value" style="color:#38BDF8">{moist:.1f}%</div>
          <div class="metric-delta" style="color:{m_color}">
            {'✅ Optimal (38–72%)' if 38 <= moist <= 72 else '⚠️ Di luar rentang optimal'}
          </div>
        </div>""", unsafe_allow_html=True)

    with c3:
        gas = latest.get("gas", 0)
        g_color = "#EF4444" if gas > 400 else "#F59E0B" if gas > 200 else "#22C55E"
        st.markdown(f"""
        <div class="metric-card" style="border-color:#86EFAC">
          <div class="metric-label">🌫️ Gas MQ-135</div>
          <div class="metric-value" style="color:#86EFAC">{gas:.0f} ppm</div>
          <div class="metric-delta" style="color:{g_color}">
            {'🔴 Kritis >400' if gas > 400 else '🟡 Aktif >200' if gas > 200 else '🟢 Normal'}
          </div>
        </div>""", unsafe_allow_html=True)

    with c4:
        ikk = latest.get("ikk", 0)
        ikk_c = ikk_color(ikk)
        st.markdown(f"""
        <div class="metric-card" style="border-color:{ikk_c}">
          <div class="metric-label">💚 Indeks Kesehatan (IKK)</div>
          <div class="metric-value" style="color:{ikk_c}">{ikk:.1f}</div>
          <div class="metric-delta">{ikk_label(ikk)}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("")

    # ── Row 2: Fase + IKK Gauge + Alerts ────────────────────
    col_fase, col_gauge, col_alert = st.columns([2, 2, 2])

    with col_fase:
        st.markdown('<div class="section-title">🔍 Fase Terdeteksi</div>',
                    unsafe_allow_html=True)
        fase_id   = latest.get("fase_pred", 0)
        fase_info = FASE_DEF.get(fase_id, FASE_DEF[0])
        fase_nama = latest.get("fase_nama", fase_info["nama"])
        fase_warna = fase_info["warna"]

        st.markdown(f"""
        <div style="background:{fase_warna}22; border:2px solid {fase_warna};
                    border-radius:12px; padding:16px; text-align:center; margin:8px 0;">
          <div style="font-size:2.5rem;">{fase_info['emoji']}</div>
          <div style="font-size:1.3rem; font-weight:700; color:{fase_warna}; margin-top:4px;">
            {fase_nama}
          </div>
          <div style="font-size:0.8rem; color:#94A3B8; margin-top:4px;">
            Fase {fase_id} dari 6 tahap kompos
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.caption(f"Last update: {latest.get('timestamp', '-')}")
        st.caption(f"Device: {latest.get('device_id', device_id)}")

    with col_gauge:
        st.markdown('<div class="section-title">📊 IKK Gauge</div>',
                    unsafe_allow_html=True)

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=ikk,
            number={"suffix": "", "font": {"color": ikk_color(ikk), "size": 32}},
            gauge={
                "axis":        {"range": [0, 100], "tickcolor": "#94A3B8",
                                "tickwidth": 1, "tickfont": {"color": "#94A3B8"}},
                "bar":         {"color": ikk_color(ikk), "thickness": 0.25},
                "bgcolor":     "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 40],  "color": hex_rgba("#7F1D1D", 0x33)},
                    {"range": [40, 60], "color": hex_rgba("#78350F", 0x33)},
                    {"range": [60, 80], "color": hex_rgba("#365314", 0x33)},
                    {"range": [80,100], "color": hex_rgba("#14532D", 0x33)},
                ],
                "threshold": {
                    "line": {"color": "white", "width": 2},
                    "thickness": 0.8,
                    "value": ikk
                },
            },
            domain={"x": [0, 1], "y": [0, 1]}
        ))
        fig_gauge.update_layout(
            height=200,
            margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            font={"color": "#D8E4F0"},
        )
        st.plotly_chart(fig_gauge, use_container_width=True,
                        config={"displayModeBar": False})

    with col_alert:
        st.markdown('<div class="section-title">⚠️ Rekomendasi Sistem</div>',
                    unsafe_allow_html=True)

        moist_val = latest.get("moisture", 50)
        gas_val   = latest.get("gas", 100)
        temp_val  = latest.get("suhu", 30)
        ikk_val   = latest.get("ikk", 70)

        alerts = []
        if moist_val < 38:
            alerts.append(("critical", "💧 Kelembapan sangat rendah — Segera siram!"))
        elif moist_val < 45:
            alerts.append(("warning", "💧 Kelembapan rendah — Monitor lebih sering"))
        elif moist_val > 72:
            alerts.append(("warning", "💧 Kelembapan tinggi — Kurangi penyiraman"))

        if gas_val > 500:
            alerts.append(("critical", "🌫️ Gas berbahaya >500ppm — Aerasi darurat!"))
        elif gas_val > 350:
            alerts.append(("warning", "🌫️ Gas tinggi — Lakukan pembalikan"))

        if temp_val > 70:
            alerts.append(("critical", "🌡️ Suhu kritis >70°C — Overheating!"))
        elif temp_val < 20 and fase_id <= 2:
            alerts.append(("warning", "🌡️ Suhu rendah — Aktivitas mikroba kurang"))

        if ikk_val < 40:
            alerts.append(("critical", "⚠️ IKK kritis — Intervensi segera!"))

        if not alerts:
            alerts.append(("ok", "✅ Semua parameter dalam kondisi normal"))

        for lvl, msg in alerts:
            css = f"alert-{lvl}"
            st.markdown(f'<div class="alert-box {css}">{msg}</div>',
                        unsafe_allow_html=True)

    # ── Row 3: Mini charts (2 jam terakhir) ─────────────────
    st.markdown('<div class="section-title">📉 Tren 2 Jam Terakhir</div>',
                unsafe_allow_html=True)

    if not history_df.empty:
        fig_mini = make_subplots(rows=1, cols=3,
                                  subplot_titles=["Suhu (°C)", "Kelembapan (%)", "Gas (ppm)"])
        cfg_list = [
            ("suhu",     "#FF8C42", 1),
            ("moisture", "#38BDF8", 2),
            ("gas",      "#86EFAC", 3),
        ]
        for col_name, color, col_idx in cfg_list:
            fig_mini.add_trace(go.Scatter(
                x=history_df["timestamp"],
                y=history_df[col_name],
                mode="lines",
                line=dict(color=color, width=2),
                fill="tozeroy",
                fillcolor=hex_rgba(color, 0x22),
                name=col_name,
                showlegend=False,
            ), row=1, col=col_idx)

        fig_mini.update_layout(
            height=200,
            margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#162232",
            font=dict(color="#D8E4F0", size=10),
        )
        fig_mini.update_xaxes(showgrid=False, tickfont=dict(size=9))
        fig_mini.update_yaxes(gridcolor="#1E3048", tickfont=dict(size=9))
        st.plotly_chart(fig_mini, use_container_width=True,
                        config={"displayModeBar": False})
    else:
        st.info("Data tren belum tersedia. Kirim beberapa data terlebih dahulu.")


# ════════════════════════════════════════════════════════════
# TAB 2: TREN SENSOR
# ════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### 📈 Visualisasi Tren Sensor")

    c_opt1, c_opt2, c_opt3 = st.columns(3)
    with c_opt1:
        hours_range = st.selectbox("Rentang Waktu",
            [1, 6, 12, 24, 48, 72, 168],
            format_func=lambda x: f"{x} jam" if x < 24 else f"{x//24} hari",
            index=3)
    with c_opt2:
        show_raw = st.toggle("Tampilkan Data Raw", value=False)
    with c_opt3:
        show_phase_bg = st.toggle("Highlight Fase", value=True)

    df_trend = fetch_history(device_id, hours=hours_range, limit=2000)

    if df_trend.empty:
        st.info("Belum ada data riwayat.")
    else:
        # Main chart: 3 sensor + IKK
        fig = make_subplots(
            rows=4, cols=1,
            shared_xaxes=True,
            row_heights=[0.3, 0.25, 0.25, 0.2],
            vertical_spacing=0.04,
            subplot_titles=[
                "🌡️ Suhu DS18B20 (°C)",
                "💧 Kelembapan Kapasitif (%)",
                "🌫️ Gas MQ-135 (ppm)",
                "💚 Indeks Kesehatan Kompos (IKK)",
            ]
        )

        # Phase background shading
        if show_phase_bg and "fase_pred" in df_trend.columns:
            prev_fase = None
            seg_start = None
            for _, row in df_trend.iterrows():
                f = int(row["fase_pred"])
                if f != prev_fase:
                    if prev_fase is not None and seg_start is not None:
                        for r_idx in [1, 2, 3, 4]:
                            fig.add_vrect(
                                x0=seg_start, x1=row["timestamp"],
                                fillcolor=FASE_DEF[prev_fase]["warna"],
                                opacity=0.08, layer="below",
                                line_width=0, row=r_idx, col=1
                            )
                    seg_start = row["timestamp"]
                    prev_fase = f

        # Sensor plots
        for row_idx, (col_name, color) in enumerate(
            [("suhu","#FF8C42"), ("moisture","#38BDF8"), ("gas","#86EFAC")], 1
        ):
            if show_raw and f"{col_name}_raw" in df_trend.columns:
                fig.add_trace(go.Scatter(
                    x=df_trend["timestamp"], y=df_trend[f"{col_name}_raw"],
                    mode="lines", name=f"{col_name} raw",
                    line=dict(color=color, width=0.6, dash="dot"),
                    opacity=0.35, showlegend=False,
                ), row=row_idx, col=1)

            fig.add_trace(go.Scatter(
                x=df_trend["timestamp"], y=df_trend[col_name],
                mode="lines", name=SENSOR_CFG[col_name]["label"],
                line=dict(color=color, width=2),
                fill="tozeroy", fillcolor=hex_rgba(color, 0x18),
            ), row=row_idx, col=1)

        # IKK
        fig.add_trace(go.Scatter(
            x=df_trend["timestamp"], y=df_trend["ikk"],
            mode="lines", name="IKK",
            line=dict(color="#FCD34D", width=2.5),
            fill="tozeroy", fillcolor=hex_rgba("#FCD34D", 0x18),
        ), row=4, col=1)

        # Threshold lines IKK
        for threshold, color_t in [(80, "#22C55E"), (60, "#F59E0B"), (40, "#EF4444")]:
            fig.add_hline(y=threshold, line_dash="dash",
                          line_color=color_t, line_width=1,
                          opacity=0.6, row=4, col=1)

        fig.update_layout(
            height=650,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#162232",
            font=dict(color="#D8E4F0"),
            legend=dict(orientation="h", y=-0.05,
                        bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
            margin=dict(l=0, r=0, t=30, b=0),
            hovermode="x unified",
        )
        fig.update_xaxes(gridcolor="#1E3048", tickfont=dict(size=9))
        fig.update_yaxes(gridcolor="#1E3048", tickfont=dict(size=9))
        st.plotly_chart(fig, use_container_width=True)

        # Distribusi per fase (violin)
        st.markdown("#### Distribusi Nilai per Fase")
        if "fase_pred" in df_trend.columns and len(df_trend) >= 10:
            df_trend["fase_label"] = df_trend["fase_pred"].map(
                lambda x: f"{FASE_DEF.get(int(x), FASE_DEF[0])['emoji']} {FASE_DEF.get(int(x), FASE_DEF[0])['nama']}"
            )
            col_v1, col_v2, col_v3 = st.columns(3)
            for col_widget, (sensor, clr) in zip(
                [col_v1, col_v2, col_v3],
                [("suhu","#FF8C42"), ("moisture","#38BDF8"), ("gas","#86EFAC")]
            ):
                with col_widget:
                    fig_v = px.violin(
                        df_trend, y=sensor, x="fase_label",
                        color="fase_label",
                        color_discrete_sequence=[
                            FASE_DEF[i]["warna"] for i in range(6)],
                        box=True, points="outliers",
                        labels={sensor: SENSOR_CFG[sensor]["label"]},
                        title=SENSOR_CFG[sensor]["label"],
                    )
                    fig_v.update_layout(
                        height=280, showlegend=False,
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="#162232",
                        font=dict(color="#D8E4F0", size=9),
                        margin=dict(l=0, r=0, t=30, b=60),
                    )
                    fig_v.update_xaxes(tickangle=-30, tickfont=dict(size=8))
                    st.plotly_chart(fig_v, use_container_width=True,
                                    config={"displayModeBar": False})


# ════════════════════════════════════════════════════════════
# TAB 3: ANALISIS SPRT
# ════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### 🔬 Analisis Sequential Probability Ratio Test (SPRT)")

    df_sprt = fetch_history(device_id, hours=72, limit=2000)
    status_data = fetch_status()

    if not df_sprt.empty and all(
        c in df_sprt.columns for c in ["sprt_cusum_t", "sprt_cusum_m", "sprt_cusum_g"]
    ):
        # SPRT parameter info
        with st.expander("📐 Parameter SPRT", expanded=False):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("α (False Positive)", "0.05")
            p2.metric("β (False Negative)", "0.10")
            p3.metric("Batas A (H₁)", "+2.944")
            p4.metric("Batas B (H₀)", "-1.946")
            st.info(
                "**SPRT Wald:** Akumulasi log-likelihood ratio Λₙ per timestep. "
                "Jika Λₙ ≥ A → transisi fase terdeteksi (H₁). "
                "Jika Λₙ ≤ B → tidak ada transisi (H₀). "
                "CUSUM di-reset setelah setiap deteksi."
            )

        # SPRT CUSUM chart
        fig_sprt = make_subplots(
            rows=3, cols=1, shared_xaxes=True,
            subplot_titles=[
                "SPRT Suhu — Λₙ Kumulatif",
                "SPRT Moisture — Λₙ Kumulatif",
                "SPRT Gas — Λₙ Kumulatif",
            ], vertical_spacing=0.08
        )

        for row_idx, (col_n, color, label) in enumerate([
            ("sprt_cusum_t", "#FF8C42", "Suhu"),
            ("sprt_cusum_m", "#38BDF8", "Moisture"),
            ("sprt_cusum_g", "#86EFAC", "Gas"),
        ], 1):
            cs = df_sprt[col_n]

            # Area fill pos/neg
            fig_sprt.add_trace(go.Scatter(
                x=df_sprt["timestamp"], y=cs.clip(lower=0),
                fill="tozeroy", fillcolor=hex_rgba("#22C55E", 0x18),
                line=dict(width=0), showlegend=False,
            ), row=row_idx, col=1)
            fig_sprt.add_trace(go.Scatter(
                x=df_sprt["timestamp"], y=cs.clip(upper=0),
                fill="tozeroy", fillcolor=hex_rgba("#EF4444", 0x18),
                line=dict(width=0), showlegend=False,
            ), row=row_idx, col=1)

            fig_sprt.add_trace(go.Scatter(
                x=df_sprt["timestamp"], y=cs,
                mode="lines", name=f"Λₙ {label}",
                line=dict(color=color, width=1.8),
            ), row=row_idx, col=1)

            # Batas A dan B
            fig_sprt.add_hline(y=2.944, line_dash="dash",
                               line_color="#22C55E", line_width=1.2,
                               opacity=0.8, row=row_idx, col=1)
            fig_sprt.add_hline(y=-1.946, line_dash="dash",
                               line_color="#EF4444", line_width=1.2,
                               opacity=0.8, row=row_idx, col=1)
            fig_sprt.add_hline(y=0, line_color="#94A3B8",
                               line_width=0.5, opacity=0.4,
                               row=row_idx, col=1)

        fig_sprt.update_layout(
            height=550,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#162232",
            font=dict(color="#D8E4F0"),
            legend=dict(orientation="h", y=-0.05,
                        bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=0, r=0, t=30, b=0),
            hovermode="x unified",
        )
        fig_sprt.update_xaxes(gridcolor="#1E3048", tickfont=dict(size=9))
        fig_sprt.update_yaxes(gridcolor="#1E3048", tickfont=dict(size=9),
                               zeroline=True, zerolinecolor="#1E3048")
        st.plotly_chart(fig_sprt, use_container_width=True)

        # Fase timeline
        st.markdown("#### 🗓️ Timeline Deteksi Fase")
        fig_phase = go.Figure()
        for fid, info in FASE_DEF.items():
            mask = df_sprt["fase_pred"] == fid
            if mask.any():
                fig_phase.add_trace(go.Scatter(
                    x=df_sprt.loc[mask, "timestamp"],
                    y=df_sprt.loc[mask, "fase_pred"],
                    mode="markers",
                    name=f"{info['emoji']} {info['nama']}",
                    marker=dict(color=info["warna"], size=6, opacity=0.8),
                ))
        fig_phase.update_layout(
            height=220,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#162232",
            font=dict(color="#D8E4F0"),
            yaxis=dict(
                tickvals=list(range(6)),
                ticktext=[f"{FASE_DEF[i]['emoji']} {FASE_DEF[i]['nama']}" for i in range(6)],
                gridcolor="#1E3048",
            ),
            xaxis=dict(gridcolor="#1E3048"),
            legend=dict(orientation="h", y=-0.25, font=dict(size=9),
                        bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_phase, use_container_width=True)

    else:
        st.info("Data SPRT belum cukup. Kirim minimal 5 data untuk mulai analisis.")

    # SPRT detection stats
    if status_data and "sprt_state" in status_data:
        st.markdown("#### 📊 Statistik Deteksi SPRT per Hipotesis")
        sprt_rows = []
        for k, v in status_data["sprt_state"].items():
            sensor, fase = k.replace("_f", " → F").split(" → ")
            sprt_rows.append({
                "Hipotesis": k,
                "Sensor": sensor,
                "Target Fase": fase,
                "CUSUM Saat Ini": round(v["cusum"], 3),
                "Total Deteksi": v["detections"],
            })
        df_sprt_stats = pd.DataFrame(sprt_rows)
        st.dataframe(
            df_sprt_stats.style.background_gradient(
                subset=["Total Deteksi"], cmap="Blues"
            ).format({"CUSUM Saat Ini": "{:.3f}"}),
            use_container_width=True, height=350
        )


# ════════════════════════════════════════════════════════════
# TAB 4: AGREGASI
# ════════════════════════════════════════════════════════════
with tab4:
    st.markdown("### 📉 Tools Agregasi Data")

    agg_col1, agg_col2 = st.columns(2)
    with agg_col1:
        agg_level = st.selectbox("Level Agregasi",
            ["hourly", "daily", "fase"],
            format_func={"hourly": "Per Jam", "daily": "Per Hari",
                         "fase": "Per Fase"}.__getitem__)
    with agg_col2:
        agg_days = st.slider("Rentang (hari)", 1, 42, 7)

    df_agg = fetch_aggregate(device_id, level=agg_level, days=agg_days)

    if df_agg.empty:
        st.info("Data agregasi belum tersedia.")
    else:
        # ── Agregasi Per Jam ─────────────────────────────────
        if agg_level == "hourly" and "hour_bucket" in df_agg.columns:
            df_agg["hour_bucket"] = pd.to_datetime(df_agg["hour_bucket"])

            fig_agg = make_subplots(
                rows=3, cols=1, shared_xaxes=True,
                subplot_titles=["Suhu μ ± σ (°C)", "Kelembapan μ ± σ (%)", "Gas μ ± σ (ppm)"],
                vertical_spacing=0.08,
            )
            params_agg = [
                ("suhu",     "#FF8C42", 1),
                ("moisture", "#38BDF8", 2),
                ("gas",      "#86EFAC", 3),
            ]
            for pname, pcolor, row_i in params_agg:
                x = df_agg["hour_bucket"]
                y_mean = df_agg[f"{pname}_mean"]
                y_std  = df_agg.get(f"{pname}_std", pd.Series([0]*len(df_agg)))
                y_max  = df_agg[f"{pname}_max"]
                y_min  = df_agg[f"{pname}_min"]

                # Confidence band (mean ± std)
                fig_agg.add_trace(go.Scatter(
                    x=pd.concat([x, x[::-1]]),
                    y=pd.concat([y_mean + y_std, (y_mean - y_std)[::-1]]),
                    fill="toself", fillcolor=hex_rgba(pcolor, 0x22),
                    line=dict(width=0), showlegend=False,
                ), row=row_i, col=1)

                # Min-Max band
                fig_agg.add_trace(go.Scatter(
                    x=pd.concat([x, x[::-1]]),
                    y=pd.concat([y_max, y_min[::-1]]),
                    fill="toself", fillcolor=hex_rgba(pcolor, 0x0F),
                    line=dict(width=0), showlegend=False,
                    name=f"{pname} range",
                ), row=row_i, col=1)

                # Mean line
                fig_agg.add_trace(go.Scatter(
                    x=x, y=y_mean,
                    mode="lines+markers",
                    name=f"{pname} μ",
                    line=dict(color=pcolor, width=2),
                    marker=dict(size=4),
                ), row=row_i, col=1)

            fig_agg.update_layout(
                height=550,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="#162232",
                font=dict(color="#D8E4F0"),
                legend=dict(orientation="h", y=-0.06,
                            bgcolor="rgba(0,0,0,0)", font=dict(size=9)),
                margin=dict(l=0, r=0, t=30, b=0),
                hovermode="x unified",
            )
            fig_agg.update_xaxes(gridcolor="#1E3048", tickfont=dict(size=9))
            fig_agg.update_yaxes(gridcolor="#1E3048", tickfont=dict(size=9))
            st.plotly_chart(fig_agg, use_container_width=True)

        # ── Agregasi Per Hari ────────────────────────────────
        elif agg_level == "daily" and "day" in df_agg.columns:
            df_agg["day"] = pd.to_datetime(df_agg["day"])

            fig_daily = go.Figure()
            for col_n, color in [("suhu_mean","#FF8C42"),
                                   ("moisture_mean","#38BDF8"),
                                   ("gas_mean","#86EFAC")]:
                if col_n in df_agg.columns:
                    fig_daily.add_trace(go.Bar(
                        x=df_agg["day"],
                        y=df_agg[col_n],
                        name=col_n.replace("_mean", "").capitalize(),
                        marker_color=color,
                        opacity=0.8,
                    ))
            fig_daily.update_layout(
                height=350, barmode="group",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="#162232",
                font=dict(color="#D8E4F0"),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
                xaxis=dict(gridcolor="#1E3048"),
                yaxis=dict(gridcolor="#1E3048"),
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig_daily, use_container_width=True)

        # ── Agregasi Per Fase ────────────────────────────────
        elif agg_level == "fase" and "fase_pred" in df_agg.columns:
            df_agg["fase_pred"] = df_agg["fase_pred"].astype(int)
            df_agg["fase_info"]  = df_agg["fase_pred"].map(
                lambda x: f"{FASE_DEF.get(x, FASE_DEF[0])['emoji']} {FASE_DEF.get(x, FASE_DEF[0])['nama']}"
            )

            fig_fase = make_subplots(
                rows=1, cols=3,
                subplot_titles=["Suhu rata-rata (°C)",
                                 "Kelembapan rata-rata (%)",
                                 "Gas rata-rata (ppm)"]
            )
            colors_fase = [FASE_DEF.get(int(x), FASE_DEF[0])["warna"]
                           for x in df_agg["fase_pred"]]

            for col_idx, (col_n, col_max) in enumerate(
                [("suhu_mean","suhu_max"), ("moisture_mean","moisture_max"),
                 ("gas_mean","gas_max")], 1
            ):
                fig_fase.add_trace(go.Bar(
                    x=df_agg["fase_info"],
                    y=df_agg[col_n],
                    marker_color=colors_fase,
                    showlegend=False,
                    error_y=dict(
                        type="data",
                        array=df_agg[col_max] - df_agg[col_n],
                        visible=True,
                        color="#94A3B8",
                    ),
                ), row=1, col=col_idx)

            fig_fase.update_layout(
                height=350,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="#162232",
                font=dict(color="#D8E4F0", size=9),
                margin=dict(l=0, r=0, t=30, b=60),
            )
            fig_fase.update_xaxes(tickangle=-25, tickfont=dict(size=8),
                                   gridcolor="#1E3048")
            fig_fase.update_yaxes(gridcolor="#1E3048")
            st.plotly_chart(fig_fase, use_container_width=True)

            # Heatmap IKK per fase
            st.markdown("#### 🌡️ IKK Rata-rata per Fase")
            if "ikk_mean" in df_agg.columns:
                df_agg_sorted = df_agg.sort_values("fase_pred")
                fig_hm = go.Figure(go.Bar(
                    x=df_agg_sorted["fase_info"],
                    y=df_agg_sorted["ikk_mean"],
                    marker_color=[ikk_color(v) for v in df_agg_sorted["ikk_mean"]],
                    text=[f"{v:.1f}" for v in df_agg_sorted["ikk_mean"]],
                    textposition="outside",
                ))
                fig_hm.add_hline(y=80, line_dash="dash", line_color="#22C55E",
                                  annotation_text="Optimal")
                fig_hm.add_hline(y=60, line_dash="dash", line_color="#F59E0B",
                                  annotation_text="Baik")
                fig_hm.update_layout(
                    height=280, yaxis_range=[0, 110],
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="#162232",
                    font=dict(color="#D8E4F0"),
                    xaxis=dict(gridcolor="#1E3048"),
                    yaxis=dict(gridcolor="#1E3048", title="IKK"),
                    margin=dict(l=0, r=0, t=10, b=60),
                )
                st.plotly_chart(fig_hm, use_container_width=True,
                                config={"displayModeBar": False})

        # Tabel ringkasan
        st.markdown("#### 📋 Tabel Agregasi")
        st.dataframe(
            df_agg.style.format({
                col: "{:.2f}" for col in df_agg.select_dtypes("float").columns
            }),
            use_container_width=True
        )

        # Download CSV
        csv = df_agg.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download CSV",
            csv,
            f"agregasi_{agg_level}_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

        # Statistik ringkas
        st.markdown("#### 📐 Ringkasan Statistik")
        numeric_cols = df_agg.select_dtypes("number").columns.tolist()
        if numeric_cols:
            st.dataframe(
                df_agg[numeric_cols].describe().T.style.format("{:.2f}"),
                use_container_width=True
            )


# ════════════════════════════════════════════════════════════
# TAB 5: KONFIGURASI
# ════════════════════════════════════════════════════════════
with tab5:
    st.markdown("### ⚙️ Konfigurasi & Tools")

    col_cfg1, col_cfg2 = st.columns(2)

    with col_cfg1:
        st.markdown("#### 🔌 Koneksi API")
        st.code(f"""
# URL Endpoint
BASE_URL = "http://<ip-server>:5000"

POST {API_BASE}/data       # Kirim data sensor
GET  {API_BASE}/latest     # Data terbaru
GET  {API_BASE}/history    # Riwayat
GET  {API_BASE}/aggregate  # Agregasi
GET  {API_BASE}/status     # Status SPRT
""", language="text")

        st.markdown("#### 📦 Format JSON ESP8266")
        st.code(json.dumps({
            "device_id": "ESP8266_01",
            "timestamp": "2024-03-15T14:30:00",
            "suhu":      54.3,
            "moisture":  48.2,
            "gas":       185.6
        }, indent=2), language="json")

        st.markdown("#### 📤 Response Contoh")
        st.code(json.dumps({
            "id": 42,
            "status": "ok",
            "analysis": {
                "fase_pred": 1,
                "fase_nama": "Termofilik Aktif",
                "ikk": 72.4
            },
            "alerts": []
        }, indent=2), language="json")

    with col_cfg2:
        st.markdown("#### 🛠️ Tools Database")

        if st.button("🔄 Clear Cache Streamlit", use_container_width=True):
            st.cache_data.clear()
            st.success("Cache dibersihkan!")

        with st.expander("⚠️ Reset Database (Development)", expanded=False):
            st.warning("Tindakan ini menghapus SEMUA data sensor dari database!")
            if st.button("🗑️ Reset Database", type="primary",
                         use_container_width=True):
                try:
                    r = requests.delete(f"{API_BASE}/reset", timeout=5)
                    if r.status_code == 200:
                        st.cache_data.clear()
                        st.success("Database berhasil direset!")
                        st.rerun()
                    else:
                        st.error(f"Error: {r.text}")
                except Exception as e:
                    st.error(f"Tidak bisa terhubung ke server: {e}")

        st.markdown("#### 📊 Status Server Detail")
        if st.button("🔍 Refresh Status", use_container_width=True):
            st.cache_data.clear()

        status_full = fetch_status()
        if status_full:
            st.json(status_full)
        else:
            st.error("Server tidak dapat dijangkau")

        st.markdown("#### 🧪 Test Koneksi")
        if st.button("📡 Ping Server", use_container_width=True):
            try:
                r = requests.get(f"{API_BASE}/health", timeout=3)
                if r.status_code == 200:
                    st.success(f"✅ Server online: {r.json()}")
                else:
                    st.error(f"❌ Server error: {r.status_code}")
            except Exception as e:
                st.error(f"❌ Tidak bisa terhubung: {e}")
