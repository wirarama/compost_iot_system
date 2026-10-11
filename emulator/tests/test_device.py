import dataclasses
import json
import stat
import unittest

from device import Device, PairingError
from tests.fake_cloud import FakeCloud
from tests.helpers import make_settings


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.cloud = FakeCloud().start()
        self.addCleanup(self.cloud.stop)
        self.settings = make_settings(self.cloud.url)
        self.device = self.new_device()

    def new_device(self, **overrides):
        # threads are never started: tests drive readings and uploads by hand
        return Device(dataclasses.replace(self.settings, **overrides))

    def pair(self, device=None, set_up=True):
        # pairing, then "Confirm registration" in the dashboard
        (device or self.device).pair(self.cloud.issue_code())
        if set_up:
            self.cloud.set_up_all()

    def reading_and_upload(self, device=None):
        device = device or self.device
        with device.lock:
            device._take_reading()
        device._upload_once()

    # ------------------------------------------------------------ pairing ---

    def test_a_new_device_has_a_fake_mac_and_no_pairing(self):
        uid = self.device.store.device_uid
        self.assertRegex(uid, r"^02(:[0-9A-F]{2}){5}$")
        self.assertEqual(self.device.status()["pairing"]["state"], "unpaired")

    def test_pairing_stores_the_key_and_starts_uploads(self):
        self.pair()
        status = self.device.status()
        self.assertEqual(status["pairing"]["state"], "paired")
        self.assertTrue(status["pairing"]["api_key_hint"].startswith("••••"))
        self.assertNotIn(self.device.credentials.api_key, json.dumps(status))

        self.reading_and_upload()
        self.assertEqual(len(self.cloud.records), 1)
        record = self.cloud.records[0]
        self.assertEqual(set(record) - {"device_id"},
                         {"timestamp", "temperature", "moisture_percent", "o2_percent", "co2_percent", "nh3_ratio"})
        self.assertTrue(record["timestamp"].endswith("+00:00"))
        self.assertEqual(self.device.status()["cloud"]["state"], "ok")

    def test_the_pairing_survives_a_restart(self):
        self.pair()
        uid = self.device.store.device_uid
        restarted = self.new_device()
        self.assertEqual(restarted.store.device_uid, uid)
        self.assertEqual(restarted.status()["pairing"]["state"], "paired")
        self.reading_and_upload(restarted)
        self.assertEqual(len(self.cloud.records), 1)

    def test_the_state_file_is_private(self):
        self.pair()
        mode = stat.S_IMODE(self.settings.state_file.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_bad_codes_are_refused_with_a_useful_message(self):
        with self.assertRaisesRegex(PairingError, "6 digits"):
            self.device.pair("12ab")
        with self.assertRaisesRegex(PairingError, "wasn't recognised"):
            self.device.pair("000000" if "000000" not in self.cloud.codes else "999999")

    def test_a_code_works_once(self):
        code = self.cloud.issue_code()
        self.device.pair(code)
        other = self.new_device(state_file=self.settings.state_file.with_name("other.json"))
        with self.assertRaisesRegex(PairingError, "already been used"):
            other.pair(code)

    def test_pairing_twice_needs_a_factory_reset(self):
        self.pair()
        with self.assertRaisesRegex(PairingError, "already paired"):
            self.pair()
        self.device.factory_reset()
        self.pair()
        self.assertEqual(self.device.status()["pairing"]["state"], "paired")

    def test_an_unreachable_cloud_is_reported(self):
        device = self.new_device(cloud_api_url="http://127.0.0.1:9",
                                 state_file=self.settings.state_file.with_name("x.json"))
        with self.assertRaisesRegex(PairingError, "Couldn't reach the cloud"):
            device.pair("123456")

    def test_factory_reset_keeps_the_hardware_id_and_the_pile(self):
        self.pair()
        uid = self.device.store.device_uid
        self.device.apply_action("turn")
        self.device.factory_reset()
        self.assertEqual(self.device.store.device_uid, uid)
        self.assertEqual(self.device.model.turns, 1)
        self.assertIsNone(self.device.credentials)

    # ------------------------------------------------------------- uplink ---

    def test_unpaired_readings_are_not_kept(self):
        self.reading_and_upload()
        self.assertEqual(self.device.status()["cloud"]["queued"], 0)
        self.assertEqual(self.cloud.records, [])

    def test_offline_readings_are_kept_and_sent_later(self):
        self.pair()
        self.cloud.fail_next = [503]
        self.reading_and_upload()
        status = self.device.status()["cloud"]
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["queued"], 1)
        self.assertGreater(status["retry_in_seconds"], 0)

        with self.device.lock:
            self.device._take_reading()
        self.device.retry_now()
        self.device._upload_once()
        self.assertEqual(len(self.cloud.records), 2)
        self.assertEqual(self.device.status()["cloud"]["queued"], 0)

    def test_a_full_queue_drops_the_oldest(self):
        self.device = self.new_device(queue_max=3)
        self.pair()
        self.reading_and_upload()  # the first upload is what tells it setup is done
        self.device.uploads_paused = True
        for _ in range(5):
            self.reading_and_upload()
        status = self.device.status()["cloud"]
        self.assertEqual((status["queued"], status["dropped_total"]), (3, 2))

    def test_a_revoked_key_unpairs_the_device(self):
        self.pair()
        self.cloud.revoke_all()
        self.reading_and_upload()
        status = self.device.status()
        self.assertEqual(status["pairing"]["state"], "unpaired")
        self.assertEqual(status["cloud"]["state"], "revoked")
        self.assertIsNone(self.new_device().credentials)  # cleared on disk too

    # ------------------------------------------------ setup in the dashboard ---

    def test_a_freshly_paired_device_waits_for_setup(self):
        self.pair(set_up=False)
        self.assertEqual(self.device.status()["cloud"]["state"], "awaiting_setup")
        self.reading_and_upload()
        status = self.device.status()["cloud"]
        self.assertEqual((status["state"], status["sent_total"]), ("awaiting_setup", 0))
        self.assertEqual(self.cloud.records, [])

    def test_waiting_for_setup_keeps_only_the_newest_reading(self):
        self.pair(set_up=False)
        for _ in range(5):
            self.reading_and_upload()
        status = self.device.status()["cloud"]
        self.assertEqual((status["queued"], status["dropped_total"]), (1, 0))
        self.assertEqual(self.device.queue[-1][1]["timestamp"], self.device.latest["timestamp"])

    def test_waiting_for_setup_checks_back_at_a_steady_pace(self):
        self.pair(set_up=False)
        for _ in range(4):
            self.reading_and_upload()
            self.device.retry_at = 0  # let the next check through straight away
        self.reading_and_upload()
        self.assertLessEqual(self.device.status()["cloud"]["retry_in_seconds"],
                             self.settings.setup_check_seconds)

    def test_readings_start_once_the_dashboard_sets_the_device_up(self):
        self.pair(set_up=False)
        self.reading_and_upload()
        self.cloud.set_up_all()
        self.device.retry_now()
        self.reading_and_upload()
        status = self.device.status()
        self.assertEqual(status["cloud"]["state"], "ok")
        self.assertEqual(len(self.cloud.records), 1)  # only the newest, not the backlog
        self.assertIn("setup finished", status["events"][0]["text"])

    def test_a_device_taken_out_of_its_bin_waits_for_setup_again(self):
        self.pair()
        self.reading_and_upload()
        self.cloud.not_set_up.add(self.device.credentials.device_id)
        self.reading_and_upload()
        self.assertEqual(self.device.status()["cloud"]["state"], "awaiting_setup")

    def test_a_batch_the_cloud_can_never_accept_is_dropped(self):
        self.pair()
        self.cloud.fail_next = [422]
        self.reading_and_upload()
        status = self.device.status()["cloud"]
        self.assertEqual((status["state"], status["queued"], status["dropped_total"]), ("rejected", 0, 1))

    def test_paused_uploads_keep_readings(self):
        self.pair()
        self.device.set_uploads_paused(True)
        self.reading_and_upload()
        self.assertEqual((self.device.status()["cloud"]["queued"], len(self.cloud.records)), (1, 0))
        self.device.set_uploads_paused(False)
        self.device._upload_once()
        self.assertEqual(len(self.cloud.records), 1)

    def test_a_key_is_never_sent_to_a_different_cloud(self):
        self.pair()
        moved = self.new_device(cloud_api_url="http://127.0.0.1:9")
        self.assertEqual(moved.status()["pairing"]["state"], "other_cloud")
        self.reading_and_upload(moved)
        self.assertEqual(moved.status()["cloud"]["queued"], 0)
        self.assertIsNone(moved.status()["cloud"]["last_attempt_at"])


if __name__ == "__main__":
    unittest.main()
