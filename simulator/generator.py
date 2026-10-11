"""
Generates fake sensor data for one compost bin, going through all the
stages defined in composting_stages.py. Temperature climbs/falls toward
a target instead of just picking random numbers, moisture slowly dries
out with occasional "turning" bumps, and the gas readings are based on
how active the pile is (linked to temperature) instead of being random
too. At the end we add some noise to make it look like real sensor data.
"""
import json
import math
import os
import random
from datetime import datetime, timedelta

import pandas as pd

from composting_stages import STAGES

# paths built off this script's own location, not wherever it's run from
THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(THIS_FOLDER, "..", "mock-data", "compostiq_mock_dataset.schema.json")
# generated datasets live next to the simulator now, not in ../mock-data
DATA_DIR = os.path.join(THIS_FOLDER, "Data")
DATA_PATH = os.path.join(DATA_DIR, "compostiq_mock_dataset.json")

# the schema is our blueprint: it says what fields exist and what range each
# one is allowed to be in, so we read it once and use it to clamp readings
with open(SCHEMA_PATH) as f:
    SCHEMA = json.load(f)
FIELD_RULES = SCHEMA["items"]["properties"]

AMBIENT_TEMP = 28.0
SAMPLE_INTERVAL_MINUTES = 30
TURNING_CHANCE_PER_DAY = 0.2
MISSED_READING_CHANCE = 0.02

TEMP_NOISE_STD = 0.5
MOISTURE_NOISE_STD = 1.0
O2_NOISE_STD = 0.3
CO2_NOISE_STD = 0.2
NH3_NOISE_STD = 0.03

# same numbers again, keyed by column name, so a caller (e.g. the simulator UI)
# can hand us a partial override without knowing our constant names
NOISE_DEFAULTS = {
    "temperature_c": TEMP_NOISE_STD,
    "moisture_pct": MOISTURE_NOISE_STD,
    "o2_pct": O2_NOISE_STD,
    "co2_pct": CO2_NOISE_STD,
    "nh3_relative": NH3_NOISE_STD,
}


def resolve_noise(noise):
    # fill any missing sensor in the override with its default
    if not noise:
        return dict(NOISE_DEFAULTS)
    resolved = dict(NOISE_DEFAULTS)
    for field in NOISE_DEFAULTS:
        if noise.get(field) is not None:
            resolved[field] = float(noise[field])
    return resolved

# highest temperature any stage reaches, used to work out how "active" the
# pile is at a given moment (find it once instead of looping every step)
GLOBAL_PEAK_TEMP = 0
for stage in STAGES:
    if stage["temp_range"][1] > GLOBAL_PEAK_TEMP:
        GLOBAL_PEAK_TEMP = stage["temp_range"][1]


def get_stage_duration(stage):
    low, likely, high = stage["duration_days"]
    return random.triangular(low, high, likely)


def get_temp_target(current_temp, temp_range):
    # head towards whichever end of the range we are already closer to
    low = temp_range[0]
    high = temp_range[1]
    midpoint = (low + high) / 2
    if current_temp <= midpoint:
        return high
    else:
        return low


def get_true_temp(days_in_stage, start_temp, target_temp, tau_days):
    # moves fast towards the target at first, then slows down (like a pot
    # of water heating up and levelling off)
    return target_temp + (start_temp - target_temp) * math.exp(-days_in_stage / tau_days)


def get_activity_level(temp):
    # 0 = pile is basically cold/inactive, 1 = pile is as hot as it ever gets
    activity = (temp - AMBIENT_TEMP) / (GLOBAL_PEAK_TEMP - AMBIENT_TEMP)
    if activity < 0:
        activity = 0
    if activity > 1:
        activity = 1
    return activity


def update_moisture(moisture, activity, moisture_range, step_days):
    low = moisture_range[0]
    high = moisture_range[1]
    span = high - low

    # moisture slowly evaporates, faster when the pile is more active
    daily_loss = span * 0.15
    moisture = moisture - daily_loss * (0.5 + activity) * step_days

    # every now and then the pile gets turned (or it rains) and moisture jumps back up
    if random.random() < TURNING_CHANCE_PER_DAY * step_days:
        moisture = moisture + random.uniform(0.4, 0.8) * span

    if moisture < low - 2:
        moisture = low - 2
    if moisture > high + 2:
        moisture = high + 2

    return moisture


def get_true_gases(activity, stage):
    o2_low, o2_high = stage["o2_range"]
    co2_low, co2_high = stage["co2_range"]
    nh3_low, nh3_high = stage["nh3_relative"]

    # more activity = more oxygen used up, so this one goes the other way
    o2 = o2_high - activity * (o2_high - o2_low)
    co2 = co2_low + activity * (co2_high - co2_low)
    nh3 = nh3_low + activity * (nh3_high - nh3_low)

    return o2, co2, nh3


def add_noise(value, std):
    return value + random.gauss(0, std)


def clamp_to_schema(value, field_name):
    # look up the allowed range for this field straight from the schema
    # file, instead of hardcoding "if x < 0: x = 0" per field
    rules = FIELD_RULES[field_name]
    if "minimum" in rules and value < rules["minimum"]:
        value = rules["minimum"]
    if "maximum" in rules and value > rules["maximum"]:
        value = rules["maximum"]
    return value


def generate_stage_data(stage, start_time, start_temp, start_moisture, days_left=None,
                        sample_interval_minutes=None, noise=None):
    if sample_interval_minutes is None:
        sample_interval_minutes = SAMPLE_INTERVAL_MINUTES
    noise = resolve_noise(noise)

    # how long this stage would naturally run for
    full_duration_days = get_stage_duration(stage)

    # how long we're actually allowed to run it for, if a day limit was set
    duration_days = full_duration_days
    if days_left is not None and duration_days > days_left:
        duration_days = days_left

    step_days = sample_interval_minutes / (24 * 60)

    num_steps = round(duration_days / step_days)
    if num_steps < 1:
        num_steps = 1

    target_temp = get_temp_target(start_temp, stage["temp_range"])
    # tau is based on the stage's natural (untruncated) duration, so cutting
    # the output short doesn't change how fast the stage actually behaves
    tau_days = full_duration_days * 0.3
    if tau_days < 0.25:
        tau_days = 0.25

    rows = []
    temp = start_temp
    moisture = start_moisture

    for step in range(num_steps):
        days_in_stage = step * step_days
        timestamp = start_time + timedelta(days=days_in_stage)

        temp = get_true_temp(days_in_stage, start_temp, target_temp, tau_days)
        activity = get_activity_level(temp)
        moisture = update_moisture(moisture, activity, stage["moisture_range"], step_days)
        o2, co2, nh3 = get_true_gases(activity, stage)

        # sometimes a reading just doesn't arrive (bad connection etc.)
        if random.random() < MISSED_READING_CHANCE:
            continue

        temperature_reading = clamp_to_schema(round(add_noise(temp, noise["temperature_c"]), 2), "temperature_c")
        moisture_reading = clamp_to_schema(round(add_noise(moisture, noise["moisture_pct"]), 2), "moisture_pct")
        o2_reading = clamp_to_schema(round(add_noise(o2, noise["o2_pct"]), 2), "o2_pct")
        co2_reading = clamp_to_schema(round(add_noise(co2, noise["co2_pct"]), 2), "co2_pct")
        nh3_reading = clamp_to_schema(round(add_noise(nh3, noise["nh3_relative"]), 3), "nh3_relative")

        rows.append({
            "timestamp": timestamp,
            "stage_id": stage["id"],
            "stage_name": stage["name"],
            "temperature_c": temperature_reading,
            "moisture_pct": moisture_reading,
            "o2_pct": o2_reading,
            "co2_pct": co2_reading,
            "nh3_relative": nh3_reading,
        })

    end_time = start_time + timedelta(days=duration_days)
    return rows, temp, moisture, end_time, duration_days


def generate_bin_data(start_time=None, seed=None, start_stage_id=0, max_days=None,
                      sample_interval_minutes=None, noise=None):
    """
    start_time    - when the simulated timeline begins (defaults to now)
    seed          - set this to get the exact same "random" data every run
    start_stage_id - which stage to start at (0 = Early Mesophilic, ... 4 = Maturation)
    max_days      - stop once this many days of data have been generated
                     (None = run all the way through to the end of Maturation)
    sample_interval_minutes - gap between readings (None = SAMPLE_INTERVAL_MINUTES)
    noise         - per-sensor standard deviations, e.g. {"temperature_c": 0.5}.
                     Anything left out falls back to NOISE_DEFAULTS.
    """
    if seed is not None:
        random.seed(seed)

    if start_time is None:
        current_time = datetime.now()
    else:
        current_time = start_time

    stages_to_run = [stage for stage in STAGES if stage["id"] >= start_stage_id]

    first_stage = stages_to_run[0]
    if first_stage["id"] == 0:
        # Stage 0 specifically can't start below ambient (see composting_stages.py)
        temp = AMBIENT_TEMP
    else:
        temp = (first_stage["temp_range"][0] + first_stage["temp_range"][1]) / 2
    moisture = (first_stage["moisture_range"][0] + first_stage["moisture_range"][1]) / 2

    all_rows = []
    days_used = 0.0

    for stage in stages_to_run:
        days_left = None
        if max_days is not None:
            days_left = max_days - days_used
            if days_left <= 0:
                break

        rows, temp, moisture, current_time, days_added = generate_stage_data(
            stage, current_time, temp, moisture, days_left,
            sample_interval_minutes=sample_interval_minutes,
            noise=noise,
        )
        all_rows = all_rows + rows
        days_used = days_used + days_added

    return pd.DataFrame(all_rows)


def dataframe_to_records(df):
    # turn the dataframe into a plain list of dicts so it reads like a normal JSON array
    records = df.to_dict(orient="records")
    for row in records:
        row["timestamp"] = row["timestamp"].isoformat()
    return records


def save_json_file(records, filepath):
    folder = os.path.dirname(filepath)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)

    with open(filepath, "w") as f:
        json.dump(records, f, indent=2)


def ask_for_settings():
    print("Stages available:")
    for stage in STAGES:
        print(" ", stage["id"], "-", stage["name"])

    start_stage_input = input("Start stage id [0]: ").strip()
    if start_stage_input == "":
        start_stage_id = 0
    else:
        start_stage_id = int(start_stage_input)

    start_date_input = input("Start date, YYYY-MM-DD [today]: ").strip()
    if start_date_input == "":
        start_time = datetime.now()
    else:
        start_time = datetime.strptime(start_date_input, "%Y-%m-%d")

    max_days_input = input("Duration in days [no limit, full cycle]: ").strip()
    if max_days_input == "":
        max_days = None
    else:
        max_days = float(max_days_input)

    return start_stage_id, start_time, max_days


if __name__ == "__main__":
    start_stage_id, start_time, max_days = ask_for_settings()

    df = generate_bin_data(seed=42, start_stage_id=start_stage_id, start_time=start_time, max_days=max_days)
    print()
    print(df.head(10))
    print("Total rows:", len(df))

    print()
    for stage in STAGES:
        stage_rows = df[df["stage_id"] == stage["id"]]
        if len(stage_rows) == 0:
            continue
        avg_temp = stage_rows["temperature_c"].mean()
        print(stage["name"], "- average temperature:", round(avg_temp, 2))

    records = dataframe_to_records(df)
    save_json_file(records, DATA_PATH)
    print()
    print("Saved data to", DATA_PATH)
