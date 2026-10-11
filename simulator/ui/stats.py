"""
The numbers behind the indicator cards.

Pure pandas, no figures and no Dash - so the same helpers can be reused by a
notebook or the backend API later without dragging the UI along.
"""

from ui.theme import PARAM_BY_KEY, PATHOGEN_KILL_TEMP, stage_envelope

# EPA Class A pathogen reduction: 55 °C held for three consecutive days.
KILL_TARGET_DAYS = 3.0


def sample_interval_days(df):
    """Median gap between readings, in days. Falls back to 30 minutes."""
    if len(df) < 2:
        return 30 / (24 * 60)
    deltas = df["timestamp"].diff().dropna()
    if deltas.empty:
        return 30 / (24 * 60)
    return float(deltas.median().total_seconds()) / 86400


def in_band_fraction(df, param_key):
    """Share of readings sitting inside the envelope their own stage defines.
    Returns None when this sensor has no envelope to compare against."""
    if df.empty:
        return None

    envelopes = {sid: stage_envelope(sid, param_key) for sid in df["stage_id"].unique()}
    if any(envelope is None for envelope in envelopes.values()):
        return None

    lows = df["stage_id"].map({sid: e[0] for sid, e in envelopes.items()})
    highs = df["stage_id"].map({sid: e[1] for sid, e in envelopes.items()})

    inside = (df[param_key] >= lows) & (df[param_key] <= highs)
    return float(inside.mean())


def parameter_stats(df, param_key):
    """Latest / min / mean / max plus the trend direction, for one sensor."""
    param = PARAM_BY_KEY[param_key]
    if df.empty:
        return {
            "key": param_key, "label": param["label"], "unit": param["unit"],
            "color": param["color"], "decimals": param["decimals"],
            "latest": None, "min": None, "mean": None, "max": None,
            "delta": None, "in_band": None,
        }

    series = df[param_key]
    # compare the last tenth of the run against the tenth before it, so the
    # arrow reflects a trend rather than one noisy reading
    window = max(len(series) // 10, 1)
    recent = float(series.iloc[-window:].mean())
    previous = float(series.iloc[-2 * window:-window].mean()) if len(series) > 2 * window else recent

    return {
        "key": param_key,
        "label": param["label"],
        "unit": param["unit"],
        "color": param["color"],
        "decimals": param["decimals"],
        "latest": float(series.iloc[-1]),
        "min": float(series.min()),
        "mean": float(series.mean()),
        "max": float(series.max()),
        "delta": recent - previous,
        "in_band": in_band_fraction(df, param_key),
    }


def longest_hot_stretch_days(df, threshold=PATHOGEN_KILL_TEMP):
    """Longest unbroken run of readings at or above the threshold, in days."""
    if df.empty:
        return 0.0

    hot = (df["temperature_c"] >= threshold).to_numpy()
    if not hot.any():
        return 0.0

    step = sample_interval_days(df)
    best = current = 0
    for is_hot in hot:
        current = current + 1 if is_hot else 0
        if current > best:
            best = current
    return round(best * step, 2)


def cycle_stats(df):
    """Headline numbers for the cycle summary strip."""
    if df.empty:
        return {
            "rows": 0, "days": 0.0, "start": None, "end": None,
            "interval_minutes": None, "peak_temp": None, "mean_temp": None,
            "hot_days": 0.0, "longest_hot_days": 0.0, "kill_progress": 0.0,
            "stage_ids": [],
        }

    step = sample_interval_days(df)
    hot_days = float((df["temperature_c"] >= PATHOGEN_KILL_TEMP).sum()) * step
    longest = longest_hot_stretch_days(df)

    return {
        "rows": int(len(df)),
        "days": round((df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400, 2),
        "start": df["timestamp"].min(),
        "end": df["timestamp"].max(),
        "interval_minutes": round(step * 24 * 60),
        "peak_temp": float(df["temperature_c"].max()),
        "mean_temp": float(df["temperature_c"].mean()),
        "hot_days": round(hot_days, 2),
        "longest_hot_days": longest,
        "kill_progress": min(longest / KILL_TARGET_DAYS, 1.0) * 100,
        "stage_ids": sorted(int(s) for s in df["stage_id"].unique()),
    }


def format_value(value, decimals, unit=""):
    if value is None or value != value:  # NaN is the only value unequal to itself
        return "—"
    text = f"{value:.{decimals}f}"
    return f"{text} {unit}".strip() if unit else text
