/*
 ============================================================
  FIRMWARE ESP8266 — Monitoring Kompos IoT v2
  Hardware : NodeMCU ESP8266 / Wemos D1 Mini
  Sensor   : DS18B20 (1-Wire) | Capacitive Soil Moisture | MQ-135
  Protokol : HTTP POST JSON ke Flask server
 ============================================================
  Wiring:
    DS18B20      → D4 (GPIO2) + 4.7kΩ pull-up ke 3.3V
    Soil Moisture → A0 (ADC)  – output analog 0-3.3V
    MQ-135       → D5 (GPIO14) via external ADC ADS1115
                   ATAU gunakan ADC multiplexer eksternal
                   (ESP8266 hanya punya 1 ADC)
    LED Status   → D7 (GPIO13) + 220Ω

  Untuk ESP8266 dengan 1 ADC:
    - Soil Moisture di A0 (langsung)
    - MQ-135 via voltage divider + sampling bergantian
      (diimplementasi dengan software multiplexing)
 ============================================================
  Library yang diperlukan (install via Library Manager):
    - ESP8266WiFi       (built-in)
    - ESP8266HTTPClient (built-in)
    - ArduinoJson       v6.x   (boccio/ArduinoJson)
    - OneWire           v2.3   (PaulStoffregen/OneWire)
    - DallasTemperature v3.9   (milesburton/DallasTemperature)
    - NTPClient         v3.2   (Arduino NTPClient)
 ============================================================
*/

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <NTPClient.h>
#include <WiFiUDP.h>
#include <EEPROM.h>

// ════════════════════════════════════════════════════════════
// KONFIGURASI — SESUAIKAN DENGAN JARINGAN & SERVER ANDA
// ════════════════════════════════════════════════════════════

// WiFi credentials
const char* WIFI_SSID     = "NamaWiFi_Anda";
const char* WIFI_PASSWORD = "PasswordWiFi_Anda";

// Server endpoint — ganti dengan IP komputer server Anda
const char* SERVER_HOST   = "192.168.1.100";   // IP Flask server
const int   SERVER_PORT   = 5000;
const char* SERVER_PATH   = "/api/data";

// Device identity
const char* DEVICE_ID     = "ESP8266_01";
const char* DEVICE_LOC    = "Bak Kompos A";

// Interval pengiriman data (milliseconds)
const unsigned long SEND_INTERVAL = 60000UL;   // 60 detik (1 menit)

// Retry settings
const int MAX_WIFI_RETRY  = 20;
const int MAX_HTTP_RETRY  = 3;
const int HTTP_TIMEOUT    = 10000;  // 10 detik

// ════════════════════════════════════════════════════════════
// PIN DEFINITIONS
// ════════════════════════════════════════════════════════════
#define PIN_DS18B20     D4   // GPIO2  - OneWire data
#define PIN_MOISTURE    A0   // ADC    - Capacitive soil moisture
#define PIN_MQ135_SEL   D5   // GPIO14 - Relay/switch selector MQ-135 ke ADC
#define PIN_LED_STATUS  D7   // GPIO13 - Status LED
#define PIN_LED_BUILTIN LED_BUILTIN  // GPIO2 built-in LED (inverted)

// Untuk MQ-135: karena ADC hanya 1, kita gunakan teknik time-multiplexing
// Ganti nilai ini jika menggunakan ADS1115 eksternal (direkomendasikan)
#define USE_ADS1115     false   // set true jika pakai ADS1115

// ════════════════════════════════════════════════════════════
// SENSOR CALIBRATION
// ════════════════════════════════════════════════════════════

// Capacitive Soil Moisture Calibration
// Ukur nilai ADC saat sensor di udara kering (dry_val)
// dan saat tercelup air sepenuhnya (wet_val)
const int MOISTURE_DRY_VAL  = 880;   // ADC saat kering (di udara)
const int MOISTURE_WET_VAL  = 380;   // ADC saat basah (tercelup air)
const float MOISTURE_MIN_PCT = 0.0;
const float MOISTURE_MAX_PCT = 100.0;

// MQ-135 Calibration (NH3/CO2/VOC equivalent)
// RO = resistance sensor di udara bersih (dihitung saat warmup)
// RL = resistance beban (load resistor, default 10kΩ)
float MQ135_RO         = 10.0;  // kΩ — dikalibrasi saat startup
const float MQ135_RL   = 10.0;  // kΩ load resistor
const float MQ135_VCC  = 3.3;   // V supply
const float MQ135_ADC_MAX = 1023.0;

// Kurva sensitifitas MQ-135 (y = a * x^b, dari datasheet)
// Parameter untuk NH3
const float MQ135_A    = 110.47;
const float MQ135_B    = -2.862;

// Offset koreksi suhu-kelembapan (opsional, sudah disederhanakan)
const float MQ135_TEMP_COEFF = 0.02;

// ════════════════════════════════════════════════════════════
// GLOBAL OBJECTS
// ════════════════════════════════════════════════════════════
OneWire           oneWire(PIN_DS18B20);
DallasTemperature ds18b20(&oneWire);

WiFiUDP           ntpUDP;
NTPClient         timeClient(ntpUDP, "pool.ntp.org", 28800); // UTC+8 (WIB)

WiFiClient        wifiClient;

// State variables
unsigned long lastSendTime    = 0;
unsigned long bootTime        = 0;
int           sendCount       = 0;
int           errorCount      = 0;
bool          mq135WarmedUp   = false;
float         lastTemperature = 25.0;
float         lastMoisture    = 50.0;
float         lastGas         = 100.0;

// Moving average buffers
const int MA_WINDOW = 5;
float tempBuffer[MA_WINDOW]  = {0};
float moistBuffer[MA_WINDOW] = {0};
float gasBuffer[MA_WINDOW]   = {0};
int bufIdx = 0;
bool bufFull = false;


// ════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS
// ════════════════════════════════════════════════════════════

void ledOn()  { digitalWrite(PIN_LED_STATUS, HIGH); }
void ledOff() { digitalWrite(PIN_LED_STATUS, LOW);  }

void blinkLED(int times, int ms_on = 100, int ms_off = 100) {
    for (int i = 0; i < times; i++) {
        ledOn();
        delay(ms_on);
        ledOff();
        if (i < times - 1) delay(ms_off);
    }
}

// Constrain float dengan soft clipping
float softClip(float val, float lo, float hi) {
    return val < lo ? lo : (val > hi ? hi : val);
}

// Moving average filter
float movingAverage(float* buf, float newVal) {
    buf[bufIdx % MA_WINDOW] = newVal;
    float sum = 0;
    int count = bufFull ? MA_WINDOW : (bufIdx + 1);
    for (int i = 0; i < count; i++) sum += buf[i];
    return sum / count;
}

// ISO 8601 timestamp
String getTimestamp() {
    timeClient.update();
    unsigned long epochTime = timeClient.getEpochTime();
    time_t rawtime = (time_t)epochTime;
    struct tm* ti  = localtime(&rawtime);
    char buf[25];
    sprintf(buf, "%04d-%02d-%02dT%02d:%02d:%02d",
            ti->tm_year + 1900, ti->tm_mon + 1, ti->tm_mday,
            ti->tm_hour, ti->tm_min, ti->tm_sec);
    return String(buf);
}

// Uptime dalam detik
unsigned long getUptime() {
    return (millis() - bootTime) / 1000;
}


// ════════════════════════════════════════════════════════════
// SENSOR READ FUNCTIONS
// ════════════════════════════════════════════════════════════

// ── DS18B20 Temperature ──────────────────────────────────────
float readTemperature() {
    ds18b20.requestTemperatures();
    delay(100);  // Tunggu konversi (750ms max untuk 12-bit, 94ms untuk 9-bit)
    float temp = ds18b20.getTempCByIndex(0);

    if (temp == DEVICE_DISCONNECTED_C || temp < -10 || temp > 85) {
        Serial.println("[WARN] DS18B20 error atau tidak terhubung");
        return lastTemperature;  // Gunakan nilai terakhir
    }
    return temp;
}

// ── Capacitive Soil Moisture ─────────────────────────────────
float readMoisture() {
    // Baca ADC beberapa kali dan rata-ratakan (reduksi noise)
    long sum = 0;
    const int SAMPLES = 10;
    for (int i = 0; i < SAMPLES; i++) {
        sum += analogRead(PIN_MOISTURE);
        delay(5);
    }
    float raw = (float)(sum / SAMPLES);

    // Konversi ke persen: map dari [DRY_VAL, WET_VAL] ke [0, 100]
    // Perhatikan: nilai ADC LEBIH TINGGI = LEBIH KERING (kapasitif sensor)
    float pct = map(raw, MOISTURE_DRY_VAL, MOISTURE_WET_VAL,
                    (long)MOISTURE_MIN_PCT, (long)MOISTURE_MAX_PCT);
    float moisture = softClip(pct, 0.0, 100.0);

    // Tambahkan koreksi non-linear (opsional, sensor tergantung)
    // moisture = moisture * 0.95 + 2.5;  // offset kalibrasi

    return moisture;
}

// ── MQ-135 Gas Sensor ────────────────────────────────────────
float readGas(float temperature, float humidity) {
    // Switching ADC ke MQ-135 (jika menggunakan multiplexer)
    digitalWrite(PIN_MQ135_SEL, HIGH);
    delay(50);

    // Baca ADC
    long sum = 0;
    const int SAMPLES = 8;
    for (int i = 0; i < SAMPLES; i++) {
        sum += analogRead(PIN_MOISTURE);  // A0 yang sama setelah switch
        delay(10);
    }
    float raw = (float)(sum / SAMPLES);

    // Kembalikan ADC ke moisture
    digitalWrite(PIN_MQ135_SEL, LOW);

    // Konversi ADC → tegangan
    float vSensor = (raw / MQ135_ADC_MAX) * MQ135_VCC;

    // Hindari pembagian dengan nol
    if (vSensor >= MQ135_VCC || vSensor <= 0) {
        Serial.println("[WARN] MQ-135 tegangan tidak valid");
        return lastGas;
    }

    // RS = resistance sensor saat ini
    float RS = ((MQ135_VCC - vSensor) / vSensor) * MQ135_RL;

    // RS/RO ratio
    float ratio = RS / MQ135_RO;
    if (ratio <= 0) return lastGas;

    // Konversi ke ppm via kurva sensitifitas (y = A * x^B)
    float ppm = MQ135_A * pow(ratio, MQ135_B);

    // Koreksi suhu-kelembapan (simplified Huang correction)
    // ppm += ppm * MQ135_TEMP_COEFF * (temperature - 20.0);

    return softClip(ppm, 0.0, 1000.0);
}

// ── Kalibrasi RO MQ-135 saat startup ────────────────────────
void calibrateMQ135() {
    Serial.println("[CAL] Kalibrasi MQ-135 (30 detik warmup)...");
    ledOn();
    delay(30000);  // MQ-135 butuh waktu panas minimal 30 detik

    float sum = 0;
    const int CAL_SAMPLES = 50;
    digitalWrite(PIN_MQ135_SEL, HIGH);
    delay(100);
    for (int i = 0; i < CAL_SAMPLES; i++) {
        float raw = analogRead(PIN_MOISTURE);
        float v   = (raw / MQ135_ADC_MAX) * MQ135_VCC;
        if (v > 0 && v < MQ135_VCC) {
            sum += ((MQ135_VCC - v) / v) * MQ135_RL;
        }
        delay(200);
    }
    digitalWrite(PIN_MQ135_SEL, LOW);

    MQ135_RO = (sum / CAL_SAMPLES) / 3.6;  // 3.6 = RS/RO di udara bersih
    MQ135_RO = constrain(MQ135_RO, 1.0, 100.0);
    mq135WarmedUp = true;

    // Simpan ke EEPROM
    EEPROM.begin(8);
    EEPROM.put(0, MQ135_RO);
    EEPROM.commit();

    Serial.print("[CAL] RO MQ-135 = ");
    Serial.print(MQ135_RO, 4);
    Serial.println(" kΩ");
    ledOff();
}

// Muat RO dari EEPROM
void loadCalibration() {
    EEPROM.begin(8);
    float stored;
    EEPROM.get(0, stored);
    if (stored > 1.0 && stored < 100.0) {
        MQ135_RO = stored;
        mq135WarmedUp = true;
        Serial.print("[CAL] RO dari EEPROM: ");
        Serial.println(MQ135_RO, 4);
    }
}


// ════════════════════════════════════════════════════════════
// WiFi CONNECTION
// ════════════════════════════════════════════════════════════
bool connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return true;

    Serial.print("[WiFi] Menghubungkan ke ");
    Serial.print(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempt = 0;
    while (WiFi.status() != WL_CONNECTED && attempt < MAX_WIFI_RETRY) {
        delay(500);
        Serial.print(".");
        digitalWrite(PIN_LED_BUILTIN, !digitalRead(PIN_LED_BUILTIN));  // Blink
        attempt++;
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("[WiFi] Terhubung! IP: ");
        Serial.println(WiFi.localIP());
        blinkLED(3, 200, 100);
        return true;
    } else {
        Serial.println("[WiFi] GAGAL terhubung!");
        blinkLED(10, 50, 50);
        return false;
    }
}


// ════════════════════════════════════════════════════════════
// HTTP POST FUNCTION
// ════════════════════════════════════════════════════════════
struct SensorData {
    float temperature;
    float moisture;
    float gas;
    String timestamp;
};

struct PostResult {
    int    httpCode;
    bool   success;
    String fase_nama;
    float  ikk;
    String message;
};

PostResult sendSensorData(const SensorData& data) {
    PostResult result = {0, false, "", 0.0, "not sent"};

    if (WiFi.status() != WL_CONNECTED) {
        if (!connectWiFi()) {
            result.message = "WiFi disconnected";
            return result;
        }
    }

    // Bangun JSON payload
    StaticJsonDocument<256> doc;
    doc["device_id"]  = DEVICE_ID;
    doc["timestamp"]  = data.timestamp;
    doc["suhu"]       = round(data.temperature * 100) / 100.0;
    doc["moisture"]   = round(data.moisture * 100) / 100.0;
    doc["gas"]        = round(data.gas * 10) / 10.0;
    doc["firmware"]   = "ESP8266_v2.0";
    doc["uptime"]     = getUptime();
    doc["wifi_rssi"]  = WiFi.RSSI();

    String jsonStr;
    serializeJson(doc, jsonStr);

    // Bangun URL
    String url = "http://";
    url += SERVER_HOST;
    url += ":";
    url += SERVER_PORT;
    url += SERVER_PATH;

    Serial.print("[HTTP] POST ke ");
    Serial.println(url);
    Serial.print("[HTTP] Payload: ");
    Serial.println(jsonStr);

    HTTPClient http;
    http.begin(wifiClient, url);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("User-Agent", "ESP8266-KomposIoT/2.0");
    http.setTimeout(HTTP_TIMEOUT);

    int httpCode = http.POST(jsonStr);
    result.httpCode = httpCode;

    if (httpCode == 201 || httpCode == 200) {
        String response = http.getString();
        Serial.print("[HTTP] Response: ");
        Serial.println(response);

        // Parse response
        StaticJsonDocument<512> respDoc;
        DeserializationError err = deserializeJson(respDoc, response);
        if (!err) {
            result.success   = true;
            result.ikk       = respDoc["analysis"]["ikk"] | 0.0f;
            result.fase_nama = respDoc["analysis"]["fase_nama"] | String("Unknown");

            // Cek alerts
            if (respDoc["alerts"].size() > 0) {
                Serial.print("[ALERT] ");
                for (int i = 0; i < respDoc["alerts"].size(); i++) {
                    Serial.print(respDoc["alerts"][i].as<String>());
                    Serial.print(" ");
                }
                Serial.println();
                // Blink LED merah cepat sebagai indikator alert
                blinkLED(5, 50, 50);
            } else {
                blinkLED(1, 200, 0);  // 1 blink = sukses
            }
        }
        result.message = "OK";
    } else {
        Serial.print("[HTTP] Error: ");
        Serial.println(httpCode);
        result.message = http.errorToString(httpCode);
        errorCount++;
        blinkLED(3, 50, 50);
    }

    http.end();
    return result;
}


// ════════════════════════════════════════════════════════════
// SETUP
// ════════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n\n");
    Serial.println("========================================");
    Serial.println("  KomposIoT Firmware v2.0");
    Serial.println("  Universitas Mataram");
    Serial.println("========================================");

    // Inisialisasi pin
    pinMode(PIN_LED_STATUS, OUTPUT);
    pinMode(PIN_MQ135_SEL,  OUTPUT);
    pinMode(PIN_LED_BUILTIN, OUTPUT);
    digitalWrite(PIN_LED_STATUS, LOW);
    digitalWrite(PIN_MQ135_SEL,  LOW);  // Default: baca moisture

    // Inisialisasi sensor DS18B20
    ds18b20.begin();
    ds18b20.setResolution(11);  // 11-bit: ±0.125°C, ~375ms konversi
    Serial.print("[DS18B20] Sensor ditemukan: ");
    Serial.println(ds18b20.getDeviceCount());

    // Muat kalibrasi dari EEPROM
    loadCalibration();

    // Hubungkan WiFi
    bootTime = millis();
    connectWiFi();

    // NTP client
    timeClient.begin();
    timeClient.update();
    Serial.print("[NTP] Waktu: ");
    Serial.println(getTimestamp());

    // Jika RO belum dikalibrasi, lakukan sekarang
    // CATATAN: Hapus komentar untuk kalibrasi ulang
    // calibrateMQ135();
    if (!mq135WarmedUp) {
        Serial.println("[CAL] Menggunakan RO default = 10.0 kΩ");
        Serial.println("[INFO] Uncomment calibrateMQ135() untuk kalibrasi ulang");
    }

    // Pre-fill buffer moving average dengan pembacaan awal
    Serial.println("[INIT] Pre-filling sensor buffers...");
    for (int i = 0; i < MA_WINDOW; i++) {
        tempBuffer[i]  = readTemperature();
        moistBuffer[i] = readMoisture();
        gasBuffer[i]   = mq135WarmedUp ? readGas(25.0, 50.0) : 100.0;
        delay(200);
    }
    bufFull = true;

    Serial.println("[INIT] Sistem siap!");
    Serial.print("[CONFIG] Interval kirim: ");
    Serial.print(SEND_INTERVAL / 1000);
    Serial.println(" detik");
    blinkLED(5, 100, 100);

    // Kirim data pertama segera
    lastSendTime = millis() - SEND_INTERVAL;
}


// ════════════════════════════════════════════════════════════
// MAIN LOOP
// ════════════════════════════════════════════════════════════
void loop() {
    unsigned long now = millis();

    // Pastikan WiFi tetap terhubung
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WiFi] Koneksi terputus, mencoba sambung ulang...");
        connectWiFi();
        delay(5000);
        return;
    }

    // Update NTP setiap 10 menit
    static unsigned long lastNTP = 0;
    if (now - lastNTP > 600000UL) {
        timeClient.update();
        lastNTP = now;
    }

    // Baca sensor setiap loop dan update moving average
    float rawTemp  = readTemperature();
    float rawMoist = readMoisture();
    float rawGas   = mq135WarmedUp ? readGas(rawTemp, rawMoist) : 100.0;

    // Update moving average
    lastTemperature = movingAverage(tempBuffer,  rawTemp);
    lastMoisture    = movingAverage(moistBuffer, rawMoist);
    lastGas         = movingAverage(gasBuffer,   rawGas);
    bufIdx++;
    if (bufIdx >= MA_WINDOW) { bufFull = true; bufIdx = 0; }

    // Kirim data sesuai interval
    if (now - lastSendTime >= SEND_INTERVAL) {
        lastSendTime = now;
        sendCount++;

        Serial.println("\n--- PEMBACAAN SENSOR ---");
        Serial.print("Suhu     : "); Serial.print(lastTemperature, 2); Serial.println(" °C");
        Serial.print("Moisture : "); Serial.print(lastMoisture, 2);    Serial.println(" %");
        Serial.print("Gas      : "); Serial.print(lastGas, 1);         Serial.println(" ppm");
        Serial.print("Timestamp: "); Serial.println(getTimestamp());
        Serial.print("Send #   : "); Serial.println(sendCount);

        SensorData sdata = {
            .temperature = lastTemperature,
            .moisture    = lastMoisture,
            .gas         = lastGas,
            .timestamp   = getTimestamp(),
        };

        // Retry logic
        PostResult result;
        for (int attempt = 1; attempt <= MAX_HTTP_RETRY; attempt++) {
            result = sendSensorData(sdata);
            if (result.success) break;
            Serial.print("[HTTP] Retry ");
            Serial.print(attempt);
            Serial.print("/");
            Serial.println(MAX_HTTP_RETRY);
            delay(2000 * attempt);  // Exponential backoff
        }

        if (result.success) {
            Serial.print("[OK] Fase: "); Serial.println(result.fase_nama);
            Serial.print("[OK] IKK : "); Serial.println(result.ikk, 1);
            Serial.print("[OK] Error total: "); Serial.println(errorCount);
        } else {
            Serial.print("[FAIL] HTTP "); Serial.print(result.httpCode);
            Serial.print(" — "); Serial.println(result.message);
        }
        Serial.println("----------------------------");

        // LED indikator status keseluruhan
        digitalWrite(PIN_LED_BUILTIN, HIGH);  // Builtin off (inverted)
    }

    // Watchdog: jika terlalu banyak error, restart
    if (errorCount > 10) {
        Serial.println("[WATCHDOG] Terlalu banyak error — restart ESP8266");
        delay(1000);
        ESP.restart();
    }

    // Hemat CPU: delay pendek
    delay(500);
}


// ════════════════════════════════════════════════════════════
// CATATAN WIRING DAN MODIFIKASI
// ════════════════════════════════════════════════════════════
/*
  WIRING DIAGRAM:
  ══════════════

  DS18B20 (TO-92 — 3 kaki):
    GND  → GND
    VCC  → 3.3V
    DATA → D4 (GPIO2) + Resistor 4.7kΩ ke 3.3V

  Capacitive Soil Moisture (5-pin module):
    VCC  → 3.3V (atau 5V jika modul mensupport)
    GND  → GND
    AOUT → A0 (ADC)
    NOTE: Sensor ini mengukur kapasitansi, bukan resistansi.
          Tidak berkarat! Output TINGGI = KERING.

  MQ-135 (4-pin module):
    VCC  → 5V (PENTING: MQ-135 butuh 5V untuk heater)
    GND  → GND
    AOUT → lihat opsi di bawah

  OPSI UNTUK 2 SENSOR ANALOG DENGAN 1 ADC ESP8266:
  ──────────────────────────────────────────────────

  OPSI A (Multiplexer Hardware - Direkomendasikan):
    Gunakan CD4051B atau CD74HC4051 8-channel analog mux
    - COM → A0 (ESP8266)
    - CH0 → Soil Moisture AOUT
    - CH1 → MQ-135 AOUT (melalui voltage divider 5V→3.3V)
    - S0, S1, S2 → D5, D6, D7 (kontrol channel)

  OPSI B (Time Multiplexing Software - Diimplementasi):
    Gunakan relay atau transistor MOSFET untuk switch
    - MQ-135 AOUT → D5 (relay COM/NO)
    - Soil Moisture AOUT → A0 (langsung)
    - Saat baca MQ-135: aktifkan relay → A0 terima dari MQ-135
    - Saat baca moisture: nonaktifkan relay → A0 terima dari moisture

  OPSI C (ADS1115 16-bit ADC eksternal - Terbaik):
    ADS1115 via I2C (SDA=D2/GPIO4, SCL=D1/GPIO5)
    - A0 → Soil Moisture AOUT
    - A1 → MQ-135 AOUT (via voltage divider)
    Ubah USE_ADS1115 menjadi true dan tambahkan library:
    #include <Adafruit_ADS1X15.h>

  VOLTAGE DIVIDER untuk MQ-135 (5V → 3.3V):
    MQ-135 AOUT → R1 (10kΩ) → node tengah → R2 (20kΩ) → GND
    node tengah → ADC input
    Vout = Vin * (R2 / (R1+R2)) = 5V * (20/30) = 3.33V ≈ 3.3V

  LED STATUS:
    D7 (GPIO13) → 220Ω → LED → GND
    Blink 1x = data terkirim OK
    Blink 3x = error HTTP
    Blink 5x = ada alert dari server
    Blink cepat saat startup = menyambung WiFi

  POWER SUPPLY:
    - ESP8266: USB 5V atau regulator 3.3V
    - MQ-135 heater butuh 5V/150mA — gunakan pin 5V (Vin) NodeMCU
    - Total konsumsi: ~300-400mA saat WiFi aktif

  TROUBLESHOOTING:
  ────────────────
  1. DS18B20 tidak terdeteksi:
     → Cek resistor 4.7kΩ pull-up
     → Cek polaritas kabel (lihat marking flat-side sensor)
  2. Moisture selalu 0% atau 100%:
     → Kalibrasi ulang MOISTURE_DRY_VAL dan MOISTURE_WET_VAL
     → Baca nilai ADC di udara dan di air dengan Serial Monitor
  3. MQ-135 nilai tidak stabil:
     → Tunggu warmup minimal 30 detik
     → Lakukan kalibrasi di udara bersih (uncomment calibrateMQ135())
  4. HTTP 0 (timeout):
     → Cek SERVER_HOST sudah benar
     → Pastikan server.py berjalan (python server.py)
     → Cek firewall — port 5000 harus terbuka
  5. WiFi tidak bisa connect:
     → Cek SSID/password (case-sensitive!)
     → ESP8266 hanya support WiFi 2.4GHz
*/
