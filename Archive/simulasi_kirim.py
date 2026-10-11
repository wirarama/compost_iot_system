"""
============================================================
 SIMULASI PENGIRIMAN DATA KOMPOS IoT → SERVER
 Mensimulasikan ESP8266 mengirim data JSON via HTTP POST
 untuk setiap fase kompos (6 fase × N sampel per fase)
============================================================
 Jalankan: python simulasi_kirim.py
 Pastikan server.py sudah berjalan di port 5000
============================================================
"""

import requests
import json
import random
import time
from datetime import datetime, timedelta

# ─── KONFIGURASI ────────────────────────────────────────────
SERVER_URL   = "http://localhost:5000/api/data"
DEVICE_ID    = "ESP8266_SIM"
SAMPEL_PER_FASE = 10      # Jumlah data yang dikirim per fase
DELAY_ANTAR_KIRIM = 0.5   # Detik jeda antar pengiriman

# ─── PROFIL SENSOR PER FASE ─────────────────────────────────
# (suhu_min, suhu_max, moisture_min, moisture_max, gas_min, gas_max)
FASE_PROFIL = {
    0: {
        "nama"    : "Mesophilik Awal",
        "emoji"   : "🌱",
        "hari"    : "0–4",
        "suhu"    : (20.0, 38.0),
        "moisture": (62.0, 75.0),
        "gas"     : (40.0, 90.0),
    },
    1: {
        "nama"    : "Termofilik Aktif",
        "emoji"   : "🔥",
        "hari"    : "4–14",
        "suhu"    : (38.0, 65.0),
        "moisture": (48.0, 68.0),
        "gas"     : (90.0, 280.0),
    },
    2: {
        "nama"    : "Puncak Dekomposisi",
        "emoji"   : "⚡",
        "hari"    : "14–20",
        "suhu"    : (52.0, 70.0),
        "moisture": (36.0, 52.0),
        "gas"     : (280.0, 580.0),
    },
    3: {
        "nama"    : "Pendinginan",
        "emoji"   : "❄️",
        "hari"    : "20–28",
        "suhu"    : (28.0, 55.0),
        "moisture": (38.0, 54.0),
        "gas"     : (120.0, 480.0),
    },
    4: {
        "nama"    : "Maturasi",
        "emoji"   : "🌾",
        "hari"    : "28–36",
        "suhu"    : (24.0, 35.0),
        "moisture": (38.0, 52.0),
        "gas"     : (45.0, 160.0),
    },
    5: {
        "nama"    : "Kompos Matang",
        "emoji"   : "✅",
        "hari"    : "36–42",
        "suhu"    : (18.0, 28.0),
        "moisture": (34.0, 50.0),
        "gas"     : (20.0, 90.0),
    },
}

# ─── FUNGSI HELPER ──────────────────────────────────────────
def nilai_sensor(lo, hi, noise=0.05):
    """Generate nilai sensor acak dalam rentang dengan sedikit noise."""
    base = random.uniform(lo, hi)
    jitter = base * noise * random.gauss(0, 1)
    return round(max(lo * 0.9, min(hi * 1.1, base + jitter)), 2)


def kirim_data(fase_id, sampel_ke, timestamp):
    """Kirim satu sampel data ke server, return (sukses, response)."""
    profil = FASE_PROFIL[fase_id]

    payload = {
        "device_id" : DEVICE_ID,
        "timestamp" : timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
        "suhu"      : nilai_sensor(*profil["suhu"]),
        "moisture"  : nilai_sensor(*profil["moisture"]),
        "gas"       : nilai_sensor(*profil["gas"]),
    }

    try:
        resp = requests.post(
            SERVER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code == 201:
            data = resp.json()
            return True, payload, data
        else:
            return False, payload, {"error": f"HTTP {resp.status_code}"}
    except requests.exceptions.ConnectionError:
        return False, payload, {"error": "Server tidak bisa dijangkau"}
    except Exception as e:
        return False, payload, {"error": str(e)}


def bar(pct, width=20):
    """Progress bar sederhana."""
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ─── MAIN SIMULASI ──────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SIMULASI PENGIRIMAN DATA KOMPOS IoT")
    print("  Target:", SERVER_URL)
    print(f"  Device: {DEVICE_ID}")
    print(f"  Sampel per fase: {SAMPEL_PER_FASE}")
    print(f"  Total sampel: {len(FASE_PROFIL) * SAMPEL_PER_FASE}")
    print("=" * 60)

    # Cek koneksi server dulu
    print("\n🔌 Mengecek koneksi server...", end=" ")
    try:
        r = requests.get("http://localhost:5000/api/health", timeout=3)
        if r.status_code == 200:
            print("✅ Server online\n")
        else:
            print(f"⚠️  Server merespons HTTP {r.status_code}\n")
    except Exception:
        print("❌ Server tidak bisa dijangkau!")
        print("   Pastikan server.py sudah dijalankan terlebih dahulu.")
        print("   Perintah: python server.py")
        return

    # Waktu mulai simulasi (mundur 42 hari dari sekarang)
    waktu_mulai = datetime.now() - timedelta(days=42)
    total_sukses = 0
    total_gagal  = 0
    rekap        = []

    # Loop setiap fase
    for fase_id in range(6):
        profil      = FASE_PROFIL[fase_id]
        n_sukses    = 0
        n_gagal     = 0
        ikk_list    = []
        fase_terdeteksi_list = []

        print(f"{'─'*60}")
        print(f"  {profil['emoji']}  Fase {fase_id}: {profil['nama']}  (Hari {profil['hari']})")
        print(f"     Suhu     : {profil['suhu'][0]}–{profil['suhu'][1]}°C")
        print(f"     Moisture : {profil['moisture'][0]}–{profil['moisture'][1]}%")
        print(f"     Gas      : {profil['gas'][0]}–{profil['gas'][1]} ppm")
        print()

        for s in range(SAMPEL_PER_FASE):
            # Timestamp: distribusikan merata dalam rentang hari fase
            hari_lo = int(profil["hari"].split("–")[0])
            hari_hi = int(profil["hari"].split("–")[1])
            hari_offset = hari_lo + (hari_hi - hari_lo) * s / max(SAMPEL_PER_FASE - 1, 1)
            ts = waktu_mulai + timedelta(days=hari_offset, hours=random.uniform(0, 23))

            sukses, payload, resp = kirim_data(fase_id, s + 1, ts)

            pct = (s + 1) / SAMPEL_PER_FASE * 100
            progress = bar(pct)

            if sukses:
                n_sukses += 1
                total_sukses += 1
                ikk_val  = resp.get("analysis", {}).get("ikk", 0)
                fase_det = resp.get("analysis", {}).get("fase_nama", "?")
                alerts   = resp.get("alerts", [])
                ikk_list.append(ikk_val)
                fase_terdeteksi_list.append(fase_det)

                alert_str = f" ⚠️ {', '.join(alerts)}" if alerts else ""
                print(
                    f"  [{progress}] #{s+1:02d}  "
                    f"T={payload['suhu']:5.1f}°C  "
                    f"M={payload['moisture']:4.1f}%  "
                    f"G={payload['gas']:5.1f}ppm  "
                    f"→ IKK={ikk_val:.1f}  [{fase_det}]{alert_str}"
                )
            else:
                n_gagal += 1
                total_gagal += 1
                err = resp.get("error", "?")
                print(f"  [{progress}] #{s+1:02d}  ❌ GAGAL: {err}")

            time.sleep(DELAY_ANTAR_KIRIM)

        # Ringkasan per fase
        ikk_rata = sum(ikk_list) / len(ikk_list) if ikk_list else 0
        if fase_terdeteksi_list:
            from collections import Counter
            fase_dominan = Counter(fase_terdeteksi_list).most_common(1)[0][0]
        else:
            fase_dominan = "-"

        print(f"\n  ✔  Sukses: {n_sukses}/{SAMPEL_PER_FASE}  |  "
              f"IKK rata-rata: {ikk_rata:.1f}  |  "
              f"Fase terdeteksi: {fase_dominan}\n")

        rekap.append({
            "fase_id"       : fase_id,
            "nama"          : profil["nama"],
            "sukses"        : n_sukses,
            "gagal"         : n_gagal,
            "ikk_rata"      : round(ikk_rata, 2),
            "fase_dominan"  : fase_dominan,
        })

    # ── Rekap akhir ────────────────────────────────────────
    print("=" * 60)
    print("  REKAP AKHIR SIMULASI")
    print("=" * 60)
    print(f"  Total dikirim  : {total_sukses + total_gagal}")
    print(f"  Sukses         : {total_sukses}")
    print(f"  Gagal          : {total_gagal}")
    print()
    print(f"  {'Fase':<25} {'Sukses':>7} {'IKK':>7} {'Terdeteksi'}")
    print(f"  {'─'*56}")
    for r in rekap:
        print(
            f"  {r['nama']:<25} {r['sukses']:>7} "
            f"{r['ikk_rata']:>7.1f}  {r['fase_dominan']}"
        )
    print()

    # Ambil status server setelah simulasi
    try:
        st = requests.get("http://localhost:5000/api/status", timeout=3).json()
        print(f"  Total rekaman di database : {st.get('total_records', '?')}")
    except Exception:
        pass

    print("=" * 60)
    print("  ✅ Simulasi selesai. Buka dashboard untuk melihat hasil.")
    print("     Perintah: streamlit run dashboard.py")
    print("=" * 60)


if __name__ == "__main__":
    random.seed(42)
    main()
