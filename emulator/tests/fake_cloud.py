"""
A stand-in for the CompostIQ API, for tests and for trying the emulator
before the real pairing endpoint exists.

It implements the contract the emulator expects (see README.md):

  POST /pairing/redeem   {code, device_uid, ...} -> {device_id, api_key}
  POST /records          x-key header, list of readings

plus routes that play the dashboard's part:

  POST /dev/codes        -> {"code": "123456"}   mint a pairing code
  POST /dev/setup        confirm every paired device's registration
                         (name + bin), so its readings are accepted
  POST /dev/revoke       revoke every key, like removing the device

As in the real flow, a device that has just redeemed a code gets 409 from
/records until it has been set up.

Keys live in memory, so restarting the fake cloud also unpairs every device.

Run it on its own:

    python -m tests.fake_cloud --port 8001        # from emulator/
    CLOUD_API_URL=http://127.0.0.1:8001 python main.py
"""
import argparse
import json
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeCloud:
    def __init__(self, host="127.0.0.1", port=0, verbose=False):
        self.lock = threading.Lock()
        self.codes = {}  # code -> used?
        self.keys = {}  # api_key -> device_id
        self.not_set_up = set()  # paired, but no name/bin yet (-> 409)
        self.records = []
        self.fail_next = []  # statuses to return for the next /records calls
        self.verbose = verbose
        self.server = ThreadingHTTPServer((host, port), self._handler())
        self.server.daemon_threads = True
        self.thread = None

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def issue_code(self):
        with self.lock:
            code = f"{secrets.randbelow(10**6):06d}"
            self.codes[code] = False
            return code

    def set_up_all(self):
        with self.lock:
            self.not_set_up.clear()

    def revoke_all(self):
        with self.lock:
            self.keys.clear()

    def _handler(self):
        cloud = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                if cloud.verbose:
                    super().log_message(format, *args)

            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"null")
                except ValueError:
                    return self._reply(422, {"detail": "invalid JSON"})

                if self.path == "/dev/codes":
                    return self._reply(201, {"code": cloud.issue_code()})
                if self.path == "/dev/setup":
                    cloud.set_up_all()
                    return self._reply(200, {"set_up": True})
                if self.path == "/dev/revoke":
                    cloud.revoke_all()
                    return self._reply(200, {"revoked": True})

                if self.path == "/pairing/redeem":
                    code = (body or {}).get("code")
                    with cloud.lock:
                        if code not in cloud.codes:
                            return self._reply(404, {"detail": "Pairing code not found or expired."})
                        if cloud.codes[code]:
                            return self._reply(409, {"detail": "Pairing code already used."})
                        cloud.codes[code] = True
                        device_id = str(uuid.uuid4())
                        api_key = secrets.token_hex(32)
                        cloud.keys[api_key] = device_id
                        cloud.not_set_up.add(device_id)
                    return self._reply(201, {"device_id": device_id, "api_key": api_key})

                if self.path == "/records":
                    with cloud.lock:
                        if cloud.fail_next:
                            status = cloud.fail_next.pop(0)
                            return self._reply(status, {"detail": f"forced {status}"})
                        device_id = cloud.keys.get(self.headers.get("x-key"))
                        if device_id is None:
                            return self._reply(401, {"detail": "Authentication failed: invalid API key."})
                        if device_id in cloud.not_set_up:
                            return self._reply(409, {"detail": "Device is not set up yet: no bin assigned."})
                        if not isinstance(body, list):
                            return self._reply(422, {"detail": "expected a list of readings"})
                        cloud.records.extend({"device_id": device_id, **r} for r in body)
                    if cloud.verbose:
                        print(f"  {len(body)} reading(s) from {device_id}: {body[-1]}")
                    return self._reply(200, body)

                self._reply(404, {"detail": "Not Found"})

        return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    cloud = FakeCloud(args.host, args.port, verbose=True)
    code = cloud.issue_code()
    print(f"fake cloud on {cloud.url}")
    print(f"pairing code: {code}   (more: curl -X POST {cloud.url}/dev/codes)")
    print(f"after pairing, confirm setup: curl -X POST {cloud.url}/dev/setup")
    try:
        cloud.server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
