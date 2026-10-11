import json
import threading
import unittest
import urllib.error
import urllib.request

from device import Device
from tests.fake_cloud import FakeCloud
from tests.helpers import make_settings
from web import make_server


class WebTest(unittest.TestCase):
    def setUp(self):
        self.cloud = FakeCloud().start()
        self.addCleanup(self.cloud.stop)
        settings = make_settings(self.cloud.url, port=0)
        self.device = Device(settings)
        server = make_server(settings, self.device)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(self, path, body=None, content_type="application/json"):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": content_type} if data else {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def test_the_page_and_its_assets_are_served(self):
        for path in ("/", "/app.js", "/style.css"):
            status, body = self.call(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body)

    def test_only_known_files_are_served(self):
        for path in ("/../config.py", "/static/../device.py", "/main.py"):
            self.assertEqual(self.call(path)[0], 404, path)

    def test_pairing_through_the_page(self):
        status, body = self.call("/api/pair", {"code": self.cloud.issue_code()})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["pairing"]["state"], "paired")

    def test_a_bad_code_comes_back_as_an_error_message(self):
        status, body = self.call("/api/pair", {"code": "12"})
        self.assertEqual(status, 400)
        self.assertIn("6 digits", json.loads(body)["error"])

    def test_actions(self):
        status, body = self.call("/api/actions/water", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["pile"]["waterings"], 1)
        self.assertEqual(self.call("/api/actions/explode", {})[0], 404)

    def test_posts_must_be_json_so_other_sites_cant_press_buttons(self):
        # a cross-site form or fetch without a preflight can only send these types
        for content_type in ("text/plain", "application/x-www-form-urlencoded"):
            status, _ = self.call("/api/reset", {}, content_type=content_type)
            self.assertEqual(status, 415, content_type)

    def test_large_bodies_are_refused(self):
        status, _ = self.call("/api/pair", {"code": "1" * 5000})
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
