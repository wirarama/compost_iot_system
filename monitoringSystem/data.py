from math import cos, exp, sin

HOURS = [f"{hour:02d}:00" for hour in range(24)]


def make_series(base, amplitude, phase=0, trend=0):
    readings = []
    for hour in range(24):
        smooth_wave = sin((hour + phase) / 3.1) * amplitude
        small_variation = cos((hour + phase) / 1.9) * amplitude * 0.25
        readings.append(round(base + smooth_wave + small_variation + hour * trend, 2))
    return readings


TELEMETRY = {
    "time": HOURS,
    "temperature": make_series(48, 5.4, 0, 0.28),
    "moisture": make_series(57, 4.2, 3, -0.22),
    "oxygen": make_series(19.6, 1.7, 5, 0.02),
    "co2": make_series(2.3, 0.65, 2, 0.018),
}

HISTORICAL = {
    "dates": [f"Day {index + 1}" for index in range(36)],
    "temperature": [
        round(24 + 42 * exp(-((index - 7) / 8) ** 2) + sin(index / 2.4) * 1.5, 1)
        for index in range(36)
    ],
    "health": [
        round(min(94, 51 + index * 1.05 + sin(index / 4) * 5), 1)
        for index in range(36)
    ],
}

METRICS = [
    {"label": "Temperature", "value": "54.2", "unit": "°C", "delta": "+2.4%", "icon": "temperature", "color": "temperature", "values": TELEMETRY["temperature"]},
    {"label": "Moisture", "value": "50.7", "unit": "%", "delta": "Within target", "icon": "droplet", "color": "moisture", "values": TELEMETRY["moisture"]},
    {"label": "Oxygen", "value": "20.4", "unit": "%", "delta": "+0.8%", "icon": "wind", "color": "oxygen", "values": TELEMETRY["oxygen"]},
    {"label": "Compost health", "value": "86", "unit": "/ 100", "delta": "Optimal", "icon": "heart", "color": "health", "values": HISTORICAL["health"][-24:]},
]

BINS = [
    {"id": "bin-1", "name": "Bin 1", "location": "UNRAM", "devices": 2, "phase": "Peak decomposition", "health": 86},
    {"id": "bin-3", "name": "Bin 3", "location": "Mataram", "devices": 1, "phase": "Active thermophilic", "health": 74},
    {"id": "school", "name": "Primary School", "location": "Lombok", "devices": 1, "phase": "Cooling", "health": 68},
]

DEVICES = [
    {"id": "outer", "name": "Outer Sensor", "location": "Bin 1", "reading": "54.2°C", "model": "ESP32-C3"},
    {"id": "inner", "name": "Inner Sensor", "location": "Bin 1", "reading": "52.8°C", "model": "ESP32-C3"},
    {"id": "school-device", "name": "School Compost", "location": "Primary School", "reading": "41.6°C", "model": "ESP8266"},
]

MAINTENANCE_TASKS = [
    {"title": "Temperature reaching high point", "detail": "Turn the compost to maintain an even breakdown.", "time": "In 2 hours"},
    {"title": "Moisture will fall below target", "detail": "Add approximately 3 litres of water.", "time": "In 2 days"},
    {"title": "Transition to maturation phase", "detail": "The compost is progressing normally.", "time": "In 10 days"},
]

COUNTRIES = [
    {"value": "ID", "label": "Indonesia"},
    {"value": "AU", "label": "Australia"},
    {"value": "NZ", "label": "New Zealand"},
    {"value": "SG", "label": "Singapore"},
    {"value": "MY", "label": "Malaysia"},
]
