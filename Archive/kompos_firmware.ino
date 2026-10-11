/*
 ============================================================
  FIRMWARE ESP32 — Monitoring Kompos IoT v2
  Hardware : Wemos D1 R32 (ESPDuino-32, ESP32-WROOM-32)
  Sensor   : DS18B20 (1-Wire) | Capacitive Soil Moisture | MQ-135
  Protokol : HTTP POST JSON ke Flask server
 ============================================================
  Wiring (label header board · GPIO):
    DS18B20       → D4 (GPIO17) + 4.7kΩ pull-up ke 3.3V
    Soil Moisture → A3 (GPIO34, ADC1) — output analog 0-3.3V, beri daya 3.3V
    MQ-135        → A2 (GPIO35, ADC1) via divider 5V→3.3V (heater @5V)
    LED Status    → D7 (GPIO14) + 220Ω
    Onboard LED   → GPIO2 (active-HIGH)

  Dua ADC native — tanpa mux:
    - Sensor analog WAJIB di ADC1 (GPIO32-39): ADC2 mati saat WiFi aktif.
    - JANGAN pakai header A0/A1 (=GPIO2/4, ADC2 + touch, tidak reliabel).
 ============================================================
  Library yang diperlukan (install via Library Manager):
    - WiFi              (built-in esp32 core)
    - HTTPClient        (built-in esp32 core)
    - ArduinoJson       v6.x   (bblanchon/ArduinoJson)
    - OneWire           v2.3   (PaulStoffregen/OneWire)
    - DallasTemperature v3.9   (milesburton/DallasTemperature)
    - NTPClient         v3.2   (Arduino NTPClient)
  Board: "WEMOS D1 R32"  (FQBN esp32:esp32:d1_uno32)
 ============================================================
*/

#include <WiFi.h>
#include <HTTPClient.h>
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
// Wemos D1 R32 (ESPDuino-32, ESP32-WROOM-32) — Arduino-Uno form factor.
// Sensor analog WAJIB di ADC1 (GPIO32-39): ADC2 mati saat WiFi aktif.
// Header board: A3=GPIO34, A2=GPIO35 (ADC1). Hindari A0/A1 (=GPIO2/4, ADC2 + touch).
#define PIN_DS18B20     17   // header D4 · GPIO17 · OneWire data (+4.7kΩ pull-up ke 3.3V)
#define PIN_MOISTURE    34   // header A3 · GPIO34 · ADC1_CH6 (input-only) soil moisture @3.3V
#define PIN_MQ135       35   // header A2 · GPIO35 · ADC1_CH7 (input-only) MQ-135 AOUT via divider 5V→3.3V
#define PIN_LED_STATUS  14   // header D7 · GPIO14 · Status LED (+220Ω)
#define PIN_LED_BUILTIN 2    // onboard LED · GPIO2 · active-HIGH (TIDAK inverted di ESP32)

// ════════════════════════════════════════════════════════════
// SENSOR CALIBRATION
// ════════════════════════════════════════════════════════════

// Capacitive Soil Moisture Calibration
// Ukur nilai ADC saat sensor di udara kering (dry_val)
// dan saat tercelup air sepenuhnya (wet_val)
const int MOISTURE_DRY_VAL  = 3200;  // ADC 12-bit saat kering — TODO re-measure (was 880 @10-bit)
const int MOISTURE_WET_VAL  = 1400;  // ADC 12-bit saat basah — TODO re-measure (was 380 @10-bit)
const float MOISTURE_MIN_PCT = 0.0;
const float MOISTURE_MAX_PCT = 100.0;

// MQ-135 Calibration (NH3/CO2/VOC equivalent)
// RO = resistance sensor di udara bersih (dihitung saat warmup)
// RL = resistance beban (load resistor, default 10kΩ)
float MQ135_RO         = 10.0;  // kΩ — dikalibrasi saat startup
const float MQ135_RL   = 10.0;  // kΩ load resistor
const float MQ135_VCC  = 5.0;   // V — supply heater MQ-135 (was 3.3, tercampur dgn ref ADC)
const float MQ135_ADC_VREF      = 3.3;    // V — full-scale ADC @ ADC_11db
const float MQ135_DIVIDER_RATIO = 1.5;    // (R1+R2)/R2 = 30k/20k, pulihkan AOUT 0-5V
const float MQ135_ADC_MAX = 4095.0; // ADC 12-bit ESP32 (was 1023.0)

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

// Struct data sensor & hasil POST — didefinisikan di atas semua fungsi
// supaya prototipe otomatis Arduino (.ino) mengenali tipenya.
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
    // MQ-135 punya pin ADC sendiri (GPIO35) — tidak ada mux lagi
    long sum = 0;
    const int SAMPLES = 8;
    for (int i = 0; i < SAMPLES; i++) {
        sum += analogRead(PIN_MQ135);
        delay(10);
    }
    float raw = (float)(sum / SAMPLES);

    // ADC → tegangan pin, lalu pulihkan AOUT 0-5V lewat rasio divider
    float vADC    = (raw / MQ135_ADC_MAX) * MQ135_ADC_VREF;
    float vSensor = vADC * MQ135_DIVIDER_RATIO;

    // Hindari pembagian dengan nol (VCC sekarang 5.0)
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
    for (int i = 0; i < CAL_SAMPLES; i++) {
        float raw = analogRead(PIN_MQ135);
        float v   = (raw / MQ135_ADC_MAX) * MQ135_ADC_VREF * MQ135_DIVIDER_RATIO;
        if (v > 0 && v < MQ135_VCC) {
            sum += ((MQ135_VCC - v) / v) * MQ135_RL;
        }
        delay(200);
    }

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
    doc["firmware"]   = "ESP32_v2.0";
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
    Serial.println("  KomposIoT Firmware v2.0 — ESP32 (Wemos D1 R32)");
    Serial.println("  Universitas Mataram");
    Serial.println("========================================");

    // Inisialisasi pin
    pinMode(PIN_LED_STATUS, OUTPUT);
    pinMode(PIN_LED_BUILTIN, OUTPUT);
    digitalWrite(PIN_LED_STATUS, LOW);

    // Konfigurasi ADC ESP32: 12-bit, atenuasi penuh (~0-3.3V) untuk kedua pin analog
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_MOISTURE, ADC_11db);
    analogSetPinAttenuation(PIN_MQ135,    ADC_11db);

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

    // Kalibrasi RO MQ-135 saat pertama kali; loadCalibration() sudah skip jika EEPROM valid
    if (!mq135WarmedUp) {
        calibrateMQ135();   // ~30 detik warmup + kalibrasi RO, disimpan ke EEPROM
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
        digitalWrite(PIN_LED_BUILTIN, LOW);   // Builtin off (active-HIGH di ESP32)
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
    DATA → header D4 (GPIO17) + Resistor 4.7kΩ ke 3.3V

  Capacitive Soil Moisture (5-pin module):
    VCC  → 3.3V
    GND  → GND
    AOUT → header A3 (GPIO34, ADC1)
    NOTE: Sensor kapasitif (bukan resistif). Tidak berkarat! Output TINGGI = KERING.
          Beri daya 3.3V supaya AOUT tetap ≤3.3V (aman untuk ADC ESP32).

  MQ-135 (4-pin module):
    VCC  → 5V (PENTING: heater MQ-135 butuh 5V)
    GND  → GND
    AOUT → voltage divider → header A2 (GPIO35, ADC1)

  DUA ADC NATIVE — TANPA MUX:
  ──────────────────────────────────────────────────
    ESP32 punya banyak channel ADC, jadi tiap sensor analog dapat pin sendiri.
    WAJIB pakai pin ADC1 (GPIO32-39): ADC2 tidak bisa dibaca saat WiFi aktif.
    Di Wemos D1 R32:  A3=GPIO34, A2=GPIO35 (ADC1, aman).
    JANGAN pakai A0/A1 (=GPIO2/4): itu ADC2 + capacitive-touch — hasil salah + gagal saat WiFi.

  VOLTAGE DIVIDER untuk MQ-135 (5V → 3.3V):
    MQ-135 AOUT → R1 (10kΩ) → node tengah → R2 (20kΩ) → GND
    node tengah → header A2 (GPIO35)
    Vout = 5V * (20 / (10+20)) = 3.33V ≈ 3.3V   (rasio pulih = 1.5 di firmware)

  LED STATUS:
    header D7 (GPIO14) → 220Ω → LED → GND
    Blink 1x = data terkirim OK
    Blink 3x = error HTTP
    Blink 5x = ada alert dari server
    Blink cepat saat startup = menyambung WiFi

  POWER SUPPLY:
    - Wemos D1 R32: USB 5V (atau 5-12V lewat jack/VIN)
    - MQ-135 heater butuh 5V/~150mA — pakai pin 5V board
    - Sensor moisture + DS18B20: 3.3V
    - Total konsumsi: ~300-500mA saat WiFi aktif

  TROUBLESHOOTING:
  ────────────────
  1. DS18B20 tidak terdeteksi:
     → Cek resistor 4.7kΩ pull-up
     → Cek polaritas kabel (lihat marking flat-side sensor)
  2. Moisture selalu 0% atau 100%:
     → Kalibrasi ulang MOISTURE_DRY_VAL dan MOISTURE_WET_VAL (skala 12-bit, 0-4095)
     → Baca nilai ADC di udara dan di air dengan Serial Monitor
  3. MQ-135 nilai konstan / tidak stabil:
     → Tunggu warmup minimal 30 detik (kalibrasi RO jalan otomatis saat boot pertama)
     → Pastikan AOUT lewat divider ke A2 (GPIO35), bukan langsung 5V
  4. Nilai analog aneh / selalu ~0 saat WiFi nyala:
     → Sensor mungkin tersambung ke pin ADC2 — pindahkan ke A2/A3 (ADC1)
  5. HTTP 0 (timeout):
     → Cek SERVER_HOST sudah benar (IP LAN server, bukan localhost)
     → Pastikan server.py berjalan (python server.py), firewall buka port 5000
  6. WiFi tidak bisa connect:
     → Cek SSID/password (case-sensitive!)
     → ESP32 hanya support WiFi 2.4GHz
*/
