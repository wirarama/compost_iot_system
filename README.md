# 🌿 KomposIoT — Sistem Monitoring Kompos Berbasis IoT

Sistem monitoring kompos real-time menggunakan ESP8266 + Flask + Streamlit  
dengan deteksi fase otomatis via **Sequential Probability Ratio Test (SPRT-CUSUM)**

---

## 📁 Struktur File

```
kompos_system/
├── server.py              ← Flask REST API server (port 5000)
├── dashboard.py           ← Streamlit dashboard (port 8501)
├── kompos_firmware.ino    ← Firmware Arduino ESP8266
├── requirements.txt       ← Python dependencies
└── README.md              ← Panduan ini
```

---

## 🚀 Cara Menjalankan

### 1. Install Dependencies Python
```bash
pip install -r requirements.txt
```

### 2. Jalankan Flask API Server
```bash
python server.py
```
Server berjalan di: `http://0.0.0.0:5000`  
Database SQLite otomatis dibuat: `kompos_data.db`

### 3. Jalankan Streamlit Dashboard
```bash
streamlit run dashboard.py
```
Dashboard terbuka di: `http://localhost:8501`

---

## 📡 API Endpoints

| Method | URL | Keterangan |
|--------|-----|-----------|
| `GET`  | `/api/health` | Health check server |
| `POST` | `/api/data` | Kirim data sensor dari ESP8266 |
| `GET`  | `/api/latest` | Data sensor terbaru |
| `GET`  | `/api/history?hours=24` | Riwayat data (N jam terakhir) |
| `GET`  | `/api/aggregate?level=hourly` | Agregasi per jam/hari/fase |
| `GET`  | `/api/status` | Status server + SPRT state |
| `DELETE` | `/api/reset` | Reset database (dev only) |

### Contoh JSON POST dari ESP8266:
```json
{
  "device_id": "ESP8266_01",
  "timestamp": "2024-03-15T14:30:00",
  "suhu": 54.3,
  "moisture": 48.2,
  "gas": 185.6
}
```

### Contoh Response:
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

## 🔌 Firmware ESP8266

### Library yang Dibutuhkan (Arduino IDE)
Pasang via **Tools → Manage Libraries**:
- `ArduinoJson` v6.x — Benoit Blanchon
- `OneWire` v2.3 — Paul Stoffregen  
- `DallasTemperature` v3.9 — Miles Burton
- `NTPClient` v3.2 — Arduino

### Konfigurasi Wajib di `kompos_firmware.ino`
```cpp
const char* WIFI_SSID     = "NamaWiFi_Anda";
const char* WIFI_PASSWORD = "PasswordWiFi_Anda";
const char* SERVER_HOST   = "192.168.1.100";  // IP komputer server
const char* DEVICE_ID     = "ESP8266_01";
```

### Wiring Sensor
| Sensor | Pin ESP8266 | Catatan |
|--------|------------|---------|
| DS18B20 DATA | D4 (GPIO2) | + resistor 4.7kΩ pull-up ke 3.3V |
| Soil Moisture AOUT | A0 | Langsung ke ADC |
| MQ-135 AOUT | via relay ke A0 | Butuh 5V untuk heater |
| LED Status | D7 (GPIO13) | + resistor 220Ω |

> **Catatan ADC:** ESP8266 hanya punya 1 pin ADC (A0).  
> Gunakan relay/transistor untuk time-multiplexing antara Soil Moisture dan MQ-135,  
> atau gunakan ADS1115 eksternal via I2C (direkomendasikan).

---

## 🔬 Metode Deteksi Fase

**6 Fase Kompos yang Dideteksi:**

| Fase | Nama | Suhu (°C) | Moisture (%) | Gas (ppm) |
|------|------|-----------|-------------|-----------|
| F0 | 🌱 Mesophilik Awal | 20–38 | 65–75 | 40–90 |
| F1 | 🔥 Termofilik Aktif | 38–68 | 48–72 | 90–280 |
| F2 | ⚡ Puncak Dekomposisi | 50–72 | 36–54 | 280–620 |
| F3 | ❄️ Pendinginan | 28–60 | 36–56 | 120–510 |
| F4 | 🌾 Maturasi | 23–37 | 38–55 | 45–170 |
| F5 | ✅ Kompos Matang | 18–30 | 33–52 | 18–95 |

**Algoritma:** SPRT-CUSUM (18 hipotesis paralel) + Bayesian Threshold Fusion  
**SPRT params:** α=0.05, β=0.10 → A=2.944, B=-1.946

---

## 📊 Fitur Dashboard

- **Live Monitor** — Metric cards real-time + gauge IKK + rekomendasi otomatis
- **Tren Sensor** — Chart interaktif multi-sensor + violin plot per fase
- **Analisis SPRT** — Visualisasi CUSUM trace + timeline fase deteksi
- **Agregasi** — Per jam/hari/fase dengan confidence band (μ±σ)
- **Konfigurasi** — Test koneksi, kirim data manual, reset database

---

## ⚠️ Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Dashboard tidak bisa konek server | Pastikan `server.py` sudah jalan, cek IP di `API_BASE` |
| DS18B20 tidak terbaca | Cek resistor pull-up 4.7kΩ dan polaritas kabel |
| Moisture selalu 0% atau 100% | Kalibrasi `MOISTURE_DRY_VAL` dan `MOISTURE_WET_VAL` |
| MQ-135 tidak stabil | Tunggu warmup 30 detik, uncomment `calibrateMQ135()` |
| HTTP timeout dari ESP8266 | Cek `SERVER_HOST`, pastikan firewall buka port 5000 |
| WiFi tidak konek | Cek SSID/password, ESP8266 hanya support 2.4GHz |

---

*Universitas Mataram — Program Studi Teknik Informatika*  
*Wirarama Wedashwara Wyrawan, 2024*
