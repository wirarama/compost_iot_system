import random
import unittest

from model import LAST_STAGE, SENSOR_LIMITS, CompostModel

STEP = 30 / 1440  # the simulator's 30-minute sample interval, in days


def run(model, days):
    for _ in range(int(days / STEP)):
        model.step(STEP)


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.model = CompostModel(random.Random(1))

    def test_a_watered_pile_goes_through_every_stage_and_cures(self):
        seen = set()
        for _ in range(int(80 / STEP)):
            self.model.step(STEP)
            seen.add(self.model.stage_id)
            if self.model.moisture < 50:
                self.model.add_water()
        self.assertEqual(seen, set(range(LAST_STAGE + 1)))
        self.assertTrue(self.model.cured)

    def test_the_hot_stages_get_hot(self):
        peak = 0
        for _ in range(int(12 / STEP)):
            self.model.step(STEP)
            peak = max(peak, self.model.true_temp())
        self.assertGreater(peak, 57)

    def test_readings_stay_inside_what_the_sensors_can_report(self):
        for _ in range(int(40 / STEP)):
            self.model.step(STEP)
            for field, value in self.model.read().items():
                low, high = SENSOR_LIMITS[field]
                self.assertTrue(low <= value <= high, f"{field}={value}")

    def test_turning_lets_air_in_and_cools_the_core(self):
        run(self.model, 6)  # into the hot stages
        before = self.model.true_values()
        self.model.turn()
        after = self.model.true_values()
        self.assertGreater(after["o2_pct"], before["o2_pct"] + 5)
        self.assertLess(after["co2_pct"], before["co2_pct"])
        self.assertLess(after["temperature_c"], before["temperature_c"])

    def test_turning_wears_off(self):
        run(self.model, 6)
        self.model.turn()
        boosted = self.model.true_values()["o2_pct"]
        run(self.model, 2)
        self.assertLess(self.model.true_values()["o2_pct"], boosted - 5)

    def test_water_raises_moisture(self):
        before = self.model.moisture
        self.model.add_water()
        self.assertAlmostEqual(self.model.moisture, before + 6)

    def test_a_dry_pile_stalls_and_water_brings_it_back(self):
        run(self.model, 6)
        self.model.moisture = 32
        dry = self.model.true_temp()
        stage_day = self.model.stage_day
        run(self.model, 1)
        self.assertLess(self.model.stage_day - stage_day, 0.3)  # clock nearly stopped
        for _ in range(4):
            self.model.add_water()
        run(self.model, 1)
        self.assertGreater(self.model.true_temp(), dry + 10)

    def test_a_soaked_pile_runs_short_of_oxygen(self):
        run(self.model, 20)  # maturation, normally airy
        normal = self.model.true_values()["o2_pct"]
        self.model.moisture = 80
        self.assertLess(self.model.true_values()["o2_pct"], normal - 5)

    def test_feedstock_starts_a_new_batch(self):
        run(self.model, 20)
        self.model.add_feedstock()
        self.assertEqual(self.model.stage_id, 0)
        self.assertEqual(self.model.batch_day, 0)

    def test_state_round_trips(self):
        run(self.model, 5)
        self.model.turn()
        copy = CompostModel.from_dict(self.model.to_dict(), random.Random(1))
        self.assertEqual(copy.to_dict(), self.model.to_dict())
        self.assertAlmostEqual(copy.true_temp(), self.model.true_temp())

    def test_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            self.model.apply("stir")


if __name__ == "__main__":
    unittest.main()
