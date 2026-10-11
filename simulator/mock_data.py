"""Generate mock CompostIQ research API data from stage definitions.

This module is intentionally deterministic by default so teammates can
regenerate the same mock files while the real ingestion/database layer is
still being built.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from stages import STAGES


METHODS = [
    {
        "method": "takakura",
        "bacterial_context": "Takakura starter culture with fermented organic scraps",
        "feedstock": ["vegetable_scraps", "fruit_peels", "rice_residue", "dry_leaves"],
    },
    {
        "method": "bokashi",
        "bacterial_context": "EM-style anaerobic inoculant, later aerated for curing",
        "feedstock": ["canteen_food_waste", "bran", "garden_trimmings"],
    },
    {
        "method": "aerated_static_pile",
        "bacterial_context": "Naturally occurring aerobic composting microbes",
        "feedstock": ["market_vegetable_waste", "dry_leaves", "cow_manure"],
    },
]

FAULTS = {
    "none": "No simulated fault",
    "stalled_pile": "Temperature stops rising and oxygen remains low",
    "sensor_dropout": "One or more readings are missing for a short period",
    "dry_pile": "Moisture falls below the healthy envelope",
    "anaerobic_risk": "Oxygen is depleted while composite gas response rises",
}


def _lerp(lo: float, hi: float, pct: float) -> float:
    return lo + (hi - lo) * pct


def _triangular_days(rng: random.Random, values: tuple[float, float, float]) -> int:
    lo, mode, hi = values
    return max(1, round(rng.triangular(lo, hi, mode)))


def _stage_value(
    rng: random.Random,
    stage: dict[str, Any],
    key: str,
    pct: float,
    profile_shape: str,
    noise: float,
) -> float:
    lo, hi = stage[key]
    if profile_shape == "rise":
        base = _lerp(lo, hi, pct)
    elif profile_shape == "fall":
        base = _lerp(hi, lo, pct)
    else:
        base = _lerp(lo, hi, 0.5 + 0.35 * math.sin(math.pi * pct))
    return base + rng.gauss(0, noise)


def _mq135_response(co2_pct: float, nh3_relative: float, rng: random.Random) -> float:
    """Simulate the MQ-135 as one drifting composite ppm-like response."""
    baseline = 55.0
    response = baseline + (co2_pct * 38.0) + (nh3_relative * 260.0)
    return max(15.0, response + rng.gauss(0, 18.0))


def _stage_shape(stage_id: int) -> str:
    if stage_id in (0, 1, 2):
        return "rise"
    if stage_id in (3, 4):
        return "fall"
    return "hump"


def _apply_fault(
    reading: dict[str, Any],
    fault_type: str,
    stage_id: int,
    sample_index: int,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if fault_type == "stalled_pile" and stage_id in (1, 2):
        reading["temperature_c"] = min(reading["temperature_c"], 42.0)
        reading["oxygen_pct"] = min(reading["oxygen_pct"], 4.5)
        flags.append({"code": "STALLED_PILE", "severity": "warning"})
    elif fault_type == "sensor_dropout" and sample_index % 9 in (0, 1):
        reading["moisture_pct"] = None
        flags.append({"code": "SENSOR_DROPOUT", "severity": "warning"})
    elif fault_type == "dry_pile" and stage_id >= 2:
        reading["moisture_pct"] = max(22.0, reading["moisture_pct"] - 14.0)
        flags.append({"code": "MOISTURE_LOW", "severity": "warning"})
    elif fault_type == "anaerobic_risk" and stage_id in (1, 2, 3):
        reading["oxygen_pct"] = min(reading["oxygen_pct"], 3.2)
        reading["mq135_composite_ppm"] += 160.0
        flags.append({"code": "ANAEROBIC_RISK", "severity": "critical"})
    return flags


def build_dataset(
    seed: int = 2408,
    start: datetime | None = None,
    sample_interval_hours: int = 6,
) -> dict[str, Any]:
    rng = random.Random(seed)
    start = start or datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)

    sites = [
        {
            "site_id": "site-unram-pilot",
            "name": "UNRAM Engineering Pilot",
            "location": {"city": "Mataram", "province": "West Nusa Tenggara", "country": "ID"},
        },
        {
            "site_id": "site-smk-lombok-01",
            "name": "SMK Pertanian Lombok Demonstration",
            "location": {"city": "Lombok Barat", "province": "West Nusa Tenggara", "country": "ID"},
        },
    ]

    bins = [
        {
            "bin_id": "bin-unram-takakura-01",
            "site_id": "site-unram-pilot",
            "device_id": "cmpiq-unram-esp32-01",
            "batch_id": "batch-2026-08-takakura-a",
            **METHODS[0],
        },
        {
            "bin_id": "bin-unram-bokashi-02",
            "site_id": "site-unram-pilot",
            "device_id": "cmpiq-unram-esp32-02",
            "batch_id": "batch-2026-08-bokashi-b",
            **METHODS[1],
        },
        {
            "bin_id": "bin-smk-aerated-01",
            "site_id": "site-smk-lombok-01",
            "device_id": "cmpiq-smk-esp32-01",
            "batch_id": "batch-2026-08-aerated-c",
            **METHODS[2],
        },
    ]

    fault_by_bin = {
        "bin-unram-takakura-01": "none",
        "bin-unram-bokashi-02": "dry_pile",
        "bin-smk-aerated-01": "anaerobic_risk",
    }

    readings: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for bin_index, compost_bin in enumerate(bins):
        timestamp = start + timedelta(hours=bin_index * 2)
        batch_readings = 0
        fault_counts: dict[str, int] = {}
        durations = [_triangular_days(rng, stage["duration_days"]) for stage in STAGES]
        total_days = sum(durations)

        for stage, duration_days in zip(STAGES, durations):
            samples = max(1, int((duration_days * 24) / sample_interval_hours))
            shape = _stage_shape(stage["id"])
            for sample_index in range(samples):
                pct = sample_index / max(samples - 1, 1)
                temp = _stage_value(rng, stage, "temp_range", pct, shape, 1.4)
                moisture = _stage_value(rng, stage, "moisture_range", pct, "fall", 1.8)
                oxygen = _stage_value(rng, stage, "o2_range", pct, "fall", 0.6)
                co2 = _stage_value(rng, stage, "co2_range", pct, "rise", 0.35)
                nh3 = _stage_value(rng, stage, "nh3_relative", pct, shape, 0.04)

                reading = {
                    "reading_id": f"rdg-{compost_bin['device_id']}-{batch_readings + 1:04d}",
                    "site_id": compost_bin["site_id"],
                    "bin_id": compost_bin["bin_id"],
                    "device_id": compost_bin["device_id"],
                    "batch_id": compost_bin["batch_id"],
                    "recorded_at": timestamp.isoformat().replace("+00:00", "Z"),
                    "sequence": batch_readings + 1,
                    "compost_context": {
                        "method": compost_bin["method"],
                        "bacterial_context": compost_bin["bacterial_context"],
                        "feedstock": compost_bin["feedstock"],
                    },
                    "telemetry": {
                        "temperature_c": round(temp, 2),
                        "moisture_pct": round(max(0.0, min(100.0, moisture)), 2),
                        "oxygen_pct": round(max(0.0, min(21.0, oxygen)), 2),
                        "co2_pct": round(max(0.0, co2), 2),
                        "nh3_relative": round(max(0.0, min(1.0, nh3)), 3),
                        "mq135_composite_ppm": round(_mq135_response(co2, nh3, rng), 2),
                    },
                    "label": {
                        "stage_id": stage["id"],
                        "stage_name": stage["name"],
                        "stage_day": round((sample_index * sample_interval_hours) / 24, 2),
                        "days_until_ready": max(0, total_days - int(batch_readings * sample_interval_hours / 24)),
                    },
                    "faults": [],
                    "quality": {
                        "simulated": True,
                        "schema_version": "2026-08-18.mock.v1",
                        "generator_seed": seed,
                    },
                }

                fault_flags = _apply_fault(
                    reading["telemetry"],
                    fault_by_bin[compost_bin["bin_id"]],
                    stage["id"],
                    sample_index,
                )
                reading["faults"] = fault_flags
                for flag in fault_flags:
                    fault_counts[flag["code"]] = fault_counts.get(flag["code"], 0) + 1

                readings.append(reading)
                batch_readings += 1
                timestamp += timedelta(hours=sample_interval_hours)

        summary.append(
            {
                "bin_id": compost_bin["bin_id"],
                "device_id": compost_bin["device_id"],
                "batch_id": compost_bin["batch_id"],
                "method": compost_bin["method"],
                "sample_count": batch_readings,
                "simulated_duration_days": total_days,
                "fault_profile": fault_by_bin[compost_bin["bin_id"]],
                "fault_counts": fault_counts,
            }
        )

    return {
        "metadata": {
            "project": "CompostIQ",
            "description": "Mock labelled composting telemetry for API and dashboard development.",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "schema_version": "2026-08-18.mock.v1",
            "source": "simulator/stages.py",
        },
        "sites": sites,
        "bins": bins,
        "stage_definitions": STAGES,
        "fault_definitions": FAULTS,
        "summary": summary,
        "readings": readings,
    }


def write_dataset(dataset: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "compostiq_mock_dataset.json").write_text(
        json.dumps(dataset, indent=2),
        encoding="utf-8",
    )

    csv_fields = [
        "reading_id",
        "site_id",
        "bin_id",
        "device_id",
        "batch_id",
        "recorded_at",
        "sequence",
        "method",
        "temperature_c",
        "moisture_pct",
        "oxygen_pct",
        "co2_pct",
        "nh3_relative",
        "mq135_composite_ppm",
        "stage_id",
        "stage_name",
        "days_until_ready",
        "fault_codes",
    ]
    with (output_dir / "compostiq_mock_readings.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_fields)
        writer.writeheader()
        for item in dataset["readings"]:
            writer.writerow(
                {
                    "reading_id": item["reading_id"],
                    "site_id": item["site_id"],
                    "bin_id": item["bin_id"],
                    "device_id": item["device_id"],
                    "batch_id": item["batch_id"],
                    "recorded_at": item["recorded_at"],
                    "sequence": item["sequence"],
                    "method": item["compost_context"]["method"],
                    **item["telemetry"],
                    "stage_id": item["label"]["stage_id"],
                    "stage_name": item["label"]["stage_name"],
                    "days_until_ready": item["label"]["days_until_ready"],
                    "fault_codes": "|".join(flag["code"] for flag in item["faults"]),
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CompostIQ mock API data.")
    parser.add_argument("--output-dir", default="mock-data", help="Directory for generated JSON and CSV.")
    parser.add_argument("--seed", type=int, default=2408, help="Deterministic random seed.")
    args = parser.parse_args()
    dataset = build_dataset(seed=args.seed)
    write_dataset(dataset, Path(args.output_dir))
    print(f"Wrote {len(dataset['readings'])} readings to {args.output_dir}")


if __name__ == "__main__":
    main()
