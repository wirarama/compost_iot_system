# 🌿 KomposIoT — IoT-Based Compost Monitoring System

Real-time compost monitoring system using ESP8266 + Flask + Streamlit  
with automatic phase detection via **Sequential Probability Ratio Test (SPRT-CUSUM)**

---

## 📁 File Structure

```
kompos_system/
├── server.py              ← Flask REST API server (port 5000)
├── dashboard.py           ← Streamlit dashboard (port 8501)
├── kompos_firmware.ino    ← ESP8266 Arduino firmware
├── requirements.txt       ← Python dependencies
└── README.md              ← This guide
```

---

## 🚀 How to Run

### 1. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Flask API Server
```bash
python server.py
```
Server runs at: `http://0.0.0.0:5000`  
SQLite database is created automatically: `kompos_data.db`

### 3. Run the Streamlit Dashboard
```bash
streamlit run dashboard.py
```
Dashboard opens at: `http://localhost:8501`

---

## 📡 API Endpoints

| Method | URL | Description |
|--------|-----|-----------|
| `GET`  | `/api/health` | Server health check |
| `POST` | `/api/data` | Send sensor data from the ESP8266 |
| `GET`  | `/api/latest` | Latest sensor data |
| `GET`  | `/api/history?hours=24` | Data history (last N hours) |
| `GET`  | `/api/aggregate?level=hourly` | Aggregation per hour/day/phase |
| `GET`  | `/api/status` | Server status + SPRT state |
| `DELETE` | `/api/reset` | Reset database (dev only) |

> **Note:** The JSON field names below (`suhu`, `moisture`, `gas`, `fase_pred`,
> `fase_nama`) are the literal keys the API expects and returns — they are part
> of the wire format and are intentionally left unchanged. `suhu` = temperature,
> `fase` = phase.

### Example POST JSON from the ESP8266:
```json
{
  "device_id": "ESP8266_01",
  "timestamp": "2024-03-15T14:30:00",
  "suhu": 54.3,
  "moisture": 48.2,
  "gas": 185.6
}
```

### Example Response:
```json
{
  "id": 42,
  "status": "ok",
  "analysis": {
    "fase_pred": 1,
    "fase_nama": "Termofilik Aktif",
    "ikk": 72.4,
    "sprt": {"cusum_t": 1.23, "cusum_m": 0.45, "cusum_g": 2.11}
  },
  "alerts": []
}
```

---

## 🔌 ESP8266 Firmware

### Required Libraries (Arduino IDE)
Install via **Tools → Manage Libraries**:
- `ArduinoJson` v6.x — Benoit Blanchon
- `OneWire` v2.3 — Paul Stoffregen  
- `DallasTemperature` v3.9 — Miles Burton
- `NTPClient` v3.2 — Arduino

### Required Configuration in `kompos_firmware.ino`
```cpp
const char* WIFI_SSID     = "YourWiFi_Name";
const char* WIFI_PASSWORD = "YourWiFi_Password";
const char* SERVER_HOST   = "192.168.1.100";  // server computer IP
const char* DEVICE_ID     = "ESP8266_01";
```

### Sensor Wiring
| Sensor | ESP8266 Pin | Notes |
|--------|------------|---------|
| DS18B20 DATA | D4 (GPIO2) | + 4.7kΩ pull-up resistor to 3.3V |
| Soil Moisture AOUT | A0 | Directly to ADC |
| MQ-135 AOUT | via relay to A0 | Needs 5V for the heater |
| Status LED | D7 (GPIO13) | + 220Ω resistor |

> **ADC Note:** The ESP8266 has only 1 ADC pin (A0).  
> Use a relay/transistor to time-multiplex between the Soil Moisture and MQ-135 sensors,  
> or use an external ADS1115 via I2C (recommended).

---

## 🔬 Phase Detection Method

**6 Compost Phases Detected:**

| Phase | Name | Temp (°C) | Moisture (%) | Gas (ppm) |
|------|------|-----------|-------------|-----------|
| F0 | 🌱 Early Mesophilic | 20–38 | 65–75 | 40–90 |
| F1 | 🔥 Active Thermophilic | 38–68 | 48–72 | 90–280 |
| F2 | ⚡ Peak Decomposition | 50–72 | 36–54 | 280–620 |
| F3 | ❄️ Cooling | 28–60 | 36–56 | 120–510 |
| F4 | 🌾 Maturation | 23–37 | 38–55 | 45–170 |
| F5 | ✅ Mature Compost | 18–30 | 33–52 | 18–95 |

**Algorithm:** SPRT-CUSUM (18 parallel hypotheses) + Bayesian Threshold Fusion  
**SPRT params:** α=0.05, β=0.10 → A=2.944, B=-1.946

---

## 📊 Dashboard Features

- **Live Monitor** — Real-time metric cards + Compost Health Index (IKK) gauge + automatic recommendations
- **Sensor Trends** — Interactive multi-sensor charts + per-phase violin plots
- **SPRT Analysis** — CUSUM trace visualization + phase detection timeline
- **Aggregation** — Per hour/day/phase with confidence band (μ±σ)
- **Configuration** — Connection test, manual data send, database reset

---

## ⚠️ Troubleshooting

| Problem | Solution |
|---------|--------|
| Dashboard can't connect to the server | Make sure `server.py` is running, check the IP in `API_BASE` |
| DS18B20 not reading | Check the 4.7kΩ pull-up resistor and cable polarity |
| Moisture always 0% or 100% | Calibrate `MOISTURE_DRY_VAL` and `MOISTURE_WET_VAL` |
| MQ-135 unstable | Wait 30 seconds for warmup, uncomment `calibrateMQ135()` |
| HTTP timeout from the ESP8266 | Check `SERVER_HOST`, make sure the firewall allows port 5000 |
| WiFi won't connect | Check SSID/password, the ESP8266 only supports 2.4GHz |

---

*Universitas Mataram — Informatics Engineering Study Program*  
*Wirarama Wedashwara Wyrawan, 2024*
