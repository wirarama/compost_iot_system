"""
A live model of one compost pile, advanced one small step at a time.

This is simulator/generator.py turned from "make a whole cycle at once" into
"what does the pile look like now". The maths is the same: temperature eases
towards the far end of each stage's range, moisture evaporates faster when
the pile is active, and the gases follow how active the pile is. The stage
table itself is shared with the simulator (composting_stages.py), so the two
can never disagree about what a stage looks like.

What's new is that the pile reacts to what you do to it:
  - turning aerates it: O2 jumps, CO2 falls, the core cools for a while,
    and a hot stage runs a little longer on the fresh oxygen
  - water raises moisture; a pile left to dry out cools off and stalls,
    and a waterlogged one goes short of oxygen
  - fresh feedstock starts a new batch from the first stage
"""
import math
import random
import sys
from pathlib import Path

try:
    from composting_stages import STAGES
except ImportError:
    # running from a checkout rather than the Docker image, where the file
    # is copied in next to this one
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
    from composting_stages import STAGES

# same per-sensor noise as the simulator
NOISE = {
    "temperature_c": 0.5,
    "moisture_pct": 1.0,
    "o2_pct": 0.3,
    "co2_pct": 0.2,
    "nh3_relative": 0.03,
}

# what the sensors can physically report, from
# mock-data/compostiq_mock_dataset.schema.json
SENSOR_LIMITS = {
    "temperature_c": (0.0, 100.0),
    "moisture_pct": (0.0, 100.0),
    "o2_pct": (0.0, 25.0),
    "co2_pct": (0.0, 15.0),
    "nh3_relative": (0.0, 1.0),
}

AIR_O2 = 20.9

# how long each after-effect takes to fade (1/e), in simulated days
AERATION_FADE_DAYS = 0.4
COOLING_FADE_DAYS = 0.3

TURN_COOLING = 0.2  # share of the heat above ambient lost when turned
TURN_MOISTURE_LOSS = 1.5
TURN_STAGE_EXTENSION_DAYS = 0.5
MAX_STAGE_EXTENSION_DAYS = 3.0
HOT_STAGES = (1, 2)

WATER_STEP_PCT = 6.0
WATER_COOLING_C = 1.5

MOISTURE_FLOOR = 15.0
MOISTURE_CEILING = 85.0
# below DRY_STALL microbes slow right down; below DRY_DEAD they stop
DRY_STALL = 45.0
DRY_DEAD = 30.0
WATERLOGGED = 65.0

GLOBAL_PEAK_TEMP = max(stage["temp_range"][1] for stage in STAGES)
LAST_STAGE = len(STAGES) - 1

ACTIONS = ("turn", "water", "feedstock")


def _clamp(value, low, high):
    return max(low, min(high, value))


class CompostModel:
    def __init__(self, rng=None, ambient_temp_c=28.0, start_stage=0):
        self.rng = rng or random.Random()
        self.ambient = ambient_temp_c
        self.batch_day = 0.0
        self.aeration = 0.0
        self.cooling = 0.0
        self.turns = 0
        self.waterings = 0

        first = STAGES[start_stage]
        if start_stage == 0:
            # stage 0 can't start below ambient (see composting_stages.py)
            temp = max(self.ambient, first["temp_range"][0])
        else:
            temp = sum(first["temp_range"]) / 2
        self.moisture = sum(first["moisture_range"]) / 2
        self._enter_stage(start_stage, temp)

    # ------------------------------------------------------------ stages ---

    def _enter_stage(self, stage_id, start_temp):
        stage = STAGES[stage_id]
        low, likely, high = stage["duration_days"]
        natural = self.rng.triangular(low, high, likely)

        self.stage_id = stage_id
        self.stage_day = 0.0
        self.stage_duration = natural
        self.stage_extension = 0.0
        self.stage_start_temp = start_temp
        # head for the far end of the range, as generator.get_temp_target does
        temp_low, temp_high = stage["temp_range"]
        self.target_temp = temp_high if start_temp <= (temp_low + temp_high) / 2 else temp_low
        # based on the natural length, so extending a stage doesn't slow it down
        self.tau = max(0.25, natural * 0.3)

    @property
    def stage(self):
        return STAGES[self.stage_id]

    @property
    def cured(self):
        return self.stage_id == LAST_STAGE and self.stage_day >= self.stage_duration

    # -------------------------------------------------------- the physics ---

    def _curve_temp(self):
        # generator.get_true_temp: fast at first, then levelling off
        return self.target_temp + (self.stage_start_temp - self.target_temp) * math.exp(
            -self.stage_day / self.tau
        )

    def _dryness(self):
        # 1 = moist enough to work normally, 0 = too dry for microbes
        return _clamp((self.moisture - DRY_DEAD) / (DRY_STALL - DRY_DEAD), 0.0, 1.0)

    def true_temp(self):
        heat = (self._curve_temp() - self.ambient) * self._dryness()
        return self.ambient + heat - self.cooling

    def _activity(self, temp):
        return _clamp((temp - self.ambient) / (GLOBAL_PEAK_TEMP - self.ambient), 0.0, 1.0)

    def true_values(self):
        temp = self.true_temp()
        activity = self._activity(temp)
        stage = self.stage
        o2_low, o2_high = stage["o2_range"]
        co2_low, co2_high = stage["co2_range"]
        nh3_low, nh3_high = stage["nh3_relative"]

        # generator.get_true_gases: more activity uses up more oxygen
        o2 = o2_high - activity * (o2_high - o2_low)
        co2 = co2_low + activity * (co2_high - co2_low)
        nh3 = nh3_low + activity * (nh3_high - nh3_low)

        # fresh air from a turn, fading back to the pile's own balance
        o2 += self.aeration * (AIR_O2 - o2) * 0.6
        co2 *= 1 - 0.5 * self.aeration
        nh3 += 0.15 * self.aeration * activity  # turning lets trapped ammonia out

        # water fills the air gaps, so a soaked pile runs short of oxygen
        soaked = max(0.0, self.moisture - WATERLOGGED)
        o2 -= soaked * 0.4
        co2 += soaked * 0.1

        return {
            "temperature_c": temp,
            "moisture_pct": self.moisture,
            "o2_pct": o2,
            "co2_pct": co2,
            "nh3_relative": nh3,
        }

    def step(self, dt_days):
        temp = self.true_temp()
        activity = self._activity(temp)
        dryness = self._dryness()

        # generator.update_moisture, without the random turning: that's
        # the user's job now
        low, high = self.stage["moisture_range"]
        daily_loss = (high - low) * 0.15
        self.moisture -= daily_loss * (0.5 + activity) * dt_days
        self.moisture = _clamp(self.moisture, MOISTURE_FLOOR, MOISTURE_CEILING)

        self.aeration *= math.exp(-dt_days / AERATION_FADE_DAYS)
        self.cooling *= math.exp(-dt_days / COOLING_FADE_DAYS)

        # a dry pile stalls: its stage clock slows along with the microbes
        self.stage_day += dt_days * max(dryness, 0.05)
        self.batch_day += dt_days

        if self.stage_day >= self.stage_duration and self.stage_id < LAST_STAGE:
            self._enter_stage(self.stage_id + 1, self._curve_temp())

    def read(self):
        """One noisy sensor reading, clamped to what the hardware can report."""
        reading = {}
        for field, value in self.true_values().items():
            noisy = value + self.rng.gauss(0, NOISE[field])
            low, high = SENSOR_LIMITS[field]
            decimals = 3 if field == "nh3_relative" else 2
            reading[field] = round(_clamp(noisy, low, high), decimals)
        return reading

    # ---------------------------------------------------------- actions ---

    def turn(self):
        self.turns += 1
        self.aeration = 1.0
        self.cooling += TURN_COOLING * max(0.0, self.true_temp() - self.ambient)
        self.moisture = _clamp(self.moisture - TURN_MOISTURE_LOSS, MOISTURE_FLOOR, MOISTURE_CEILING)
        if self.stage_id in HOT_STAGES and self.stage_extension < MAX_STAGE_EXTENSION_DAYS:
            self.stage_extension += TURN_STAGE_EXTENSION_DAYS
            self.stage_duration += TURN_STAGE_EXTENSION_DAYS

    def add_water(self):
        self.waterings += 1
        self.moisture = _clamp(self.moisture + WATER_STEP_PCT, MOISTURE_FLOOR, MOISTURE_CEILING)
        self.cooling += WATER_COOLING_C

    def add_feedstock(self):
        # fresh greens: a new batch starts warming from where the pile is now
        stage0 = STAGES[0]
        start = _clamp(self.true_temp(), self.ambient, sum(stage0["temp_range"]) / 2)
        self.moisture = _clamp(self.moisture + 3.0, MOISTURE_FLOOR, MOISTURE_CEILING)
        self.cooling = 0.0
        self.batch_day = 0.0
        self._enter_stage(0, start)

    def apply(self, action):
        if action == "turn":
            self.turn()
        elif action == "water":
            self.add_water()
        elif action == "feedstock":
            self.add_feedstock()
        else:
            raise ValueError(f"unknown action {action!r}")

    # ------------------------------------------------------- persistence ---

    _SAVED = (
        "batch_day", "aeration", "cooling", "turns", "waterings", "moisture",
        "stage_id", "stage_day", "stage_duration", "stage_extension",
        "stage_start_temp", "target_temp", "tau",
    )

    def to_dict(self):
        return {name: getattr(self, name) for name in self._SAVED}

    @classmethod
    def from_dict(cls, saved, rng=None, ambient_temp_c=28.0):
        model = cls(rng=rng, ambient_temp_c=ambient_temp_c)
        for name in cls._SAVED:
            setattr(model, name, type(getattr(model, name))(saved[name]))
        if not 0 <= model.stage_id <= LAST_STAGE:
            raise ValueError("saved stage_id out of range")
        return model

    def summary(self):
        """What the emulator knows but a real device never would."""
        return {
            "stage_id": self.stage_id,
            "stage_name": self.stage["name"],
            "stage_day": round(self.stage_day, 2),
            "stage_length_days": round(self.stage_duration, 2),
            "batch_day": round(self.batch_day, 2),
            "cured": self.cured,
            "turns": self.turns,
            "waterings": self.waterings,
            "true_temperature_c": round(self.true_temp(), 2),
            # the healthy band for each sensor at this stage
            "expected": {
                "temperature_c": self.stage["temp_range"],
                "moisture_pct": self.stage["moisture_range"],
                "o2_pct": self.stage["o2_range"],
                "co2_pct": self.stage["co2_range"],
                "nh3_relative": self.stage["nh3_relative"],
            },
            "stages": [stage["name"] for stage in STAGES],
        }
