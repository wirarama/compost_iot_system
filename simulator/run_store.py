"""
Where generated cycles get parked.

Every time the UI presses Generate we write two files into simulator/Data/:

    <run_id>.json       the readings themselves, an array that validates
                        against mock-data/compostiq_mock_dataset.schema.json
    <run_id>.meta.json  the settings that produced it plus a small summary,
                        kept separate because the schema forbids extra keys

Keeping runs on disk (instead of in a dcc.Store) means the browser never has
to carry thousands of readings around, and the tabs survive a restart.
"""
import json
import os
from datetime import datetime

import pandas as pd

from generator import DATA_DIR

RUNS_DIR = DATA_DIR
META_SUFFIX = ".meta.json"

# reading the same run for every callback would be wasteful, so hold onto the
# last parse. keyed by (run_id, mtime) so an edited file is never served stale.
_frame_cache = {}
_CACHE_LIMIT = 12


def ensure_dir():
    if not os.path.exists(RUNS_DIR):
        os.makedirs(RUNS_DIR)


def data_path(run_id):
    return os.path.join(RUNS_DIR, run_id + ".json")


def meta_path(run_id):
    return os.path.join(RUNS_DIR, run_id + META_SUFFIX)


def new_run_id(when=None):
    if when is None:
        when = datetime.now()
    return "run_" + when.strftime("%Y%m%d-%H%M%S-%f")[:-3]


def summarise(df):
    """The handful of numbers the cycle header and stat cards need."""
    if df.empty:
        return {"rows": 0, "start": None, "end": None, "days": 0.0, "stages": []}

    start = df["timestamp"].min()
    end = df["timestamp"].max()

    stages = []
    for stage_id, chunk in df.groupby("stage_id", sort=True):
        stage_start = chunk["timestamp"].min()
        stage_end = chunk["timestamp"].max()
        stages.append({
            "stage_id": int(stage_id),
            "stage_name": chunk["stage_name"].iloc[0],
            "rows": int(len(chunk)),
            "start": stage_start.isoformat(),
            "end": stage_end.isoformat(),
            "days": round((stage_end - stage_start).total_seconds() / 86400, 3),
            "mean_temp": round(float(chunk["temperature_c"].mean()), 2),
            "peak_temp": round(float(chunk["temperature_c"].max()), 2),
        })

    return {
        "rows": int(len(df)),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": round((end - start).total_seconds() / 86400, 2),
        "stages": stages,
    }


def save_run(records, config, label, run_id=None, created_at=None):
    """Write the readings + a meta sidecar, and hand back the meta dict."""
    ensure_dir()

    if created_at is None:
        created_at = datetime.now()
    if run_id is None:
        run_id = new_run_id(created_at)

    with open(data_path(run_id), "w") as f:
        json.dump(records, f, indent=2)

    df = pd.DataFrame(records)
    if not df.empty:
        # format is pinned because a timestamp landing exactly on the second
        # serialises without microseconds, and pandas infers one format for
        # the whole column from its first value
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")

    meta = {
        "run_id": run_id,
        "label": label,
        "created_at": created_at.isoformat(),
        "config": config,
        "summary": summarise(df),
    }
    with open(meta_path(run_id), "w") as f:
        json.dump(meta, f, indent=2)

    return meta


def list_runs():
    """Every run currently in Data/, oldest first. Skips anything unreadable."""
    ensure_dir()

    runs = []
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(META_SUFFIX):
            continue
        run_id = name[: -len(META_SUFFIX)]
        if not os.path.exists(data_path(run_id)):
            continue
        try:
            with open(os.path.join(RUNS_DIR, name)) as f:
                meta = json.load(f)
        except (ValueError, OSError):
            continue
        meta["run_id"] = run_id
        runs.append(meta)

    runs.sort(key=lambda m: m.get("created_at", ""))
    return runs


def load_dataframe(run_id):
    """Readings for one run, as a DataFrame with a real datetime column."""
    path = data_path(run_id)
    if not os.path.exists(path):
        return pd.DataFrame()

    key = (run_id, os.path.getmtime(path))
    if key in _frame_cache:
        return _frame_cache[key]

    with open(path) as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
        df = df.sort_values("timestamp").reset_index(drop=True)

    # drop any older parse of this same run, then trim the cache
    for cached in [k for k in _frame_cache if k[0] == run_id]:
        del _frame_cache[cached]
    while len(_frame_cache) >= _CACHE_LIMIT:
        del _frame_cache[next(iter(_frame_cache))]
    _frame_cache[key] = df

    return df


def delete_run(run_id):
    for path in (data_path(run_id), meta_path(run_id)):
        if os.path.exists(path):
            os.remove(path)
    for cached in [k for k in _frame_cache if k[0] == run_id]:
        del _frame_cache[cached]
