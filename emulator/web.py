"""
The device's local web page, as a real device would serve once it's on Wi-Fi.

One static page plus a handful of JSON routes:

  GET  /                   the page (static/index.html, app.js, style.css)
  GET  /api/health         liveness check for Docker
  GET  /api/status         everything the page shows
  POST /api/pair           {"code": "123456"}
  POST /api/actions/<a>    a = turn | water | feedstock
  POST /api/uploads        {"paused": true | false}
  POST /api/uploads/retry  skip the current back-off
  POST /api/reset          factory reset (clears pairing only)

Every POST must be sent as application/json. Browsers can't send that
cross-site without a CORS preflight, which this server never approves, so
another website can't press the device's buttons from a visitor's browser.
"""
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from device import PairingError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY_BYTES = 2048


def make_handler(device):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CompostIQDevice"
        sys_version = ""

        def log_message(self, format, *args):
            log.debug("%s - %s", self.address_string(), format % args)

        # ---------------------------------------------------------- output ---

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _error(self, status, message):
            self._json(status, {"error": message})

        # ----------------------------------------------------------- input ---

        def _read_json(self):
            if self.headers.get_content_type() != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
                return None
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "body isn't valid JSON")
                return None
            if not isinstance(body, dict):
                self._error(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
                return None
            return body

        # ---------------------------------------------------------- routes ---

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), content_type)
            elif path == "/api/status":
                self._json(HTTPStatus.OK, device.status())
            elif path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            body = self._read_json()
            if body is None:
                return

            if path == "/api/pair":
                code = body.get("code")
                if not isinstance(code, str):
                    return self._error(HTTPStatus.BAD_REQUEST, "code must be a string")
                try:
                    device.pair(code)
                except PairingError as error:
                    return self._error(error.status, error.message)
            elif path.startswith("/api/actions/"):
                try:
                    device.apply_action(path.removeprefix("/api/actions/"))
                except ValueError:
                    return self._error(HTTPStatus.NOT_FOUND, "unknown action")
            elif path == "/api/uploads":
                paused = body.get("paused")
                if not isinstance(paused, bool):
                    return self._error(HTTPStatus.BAD_REQUEST, "paused must be true or false")
                device.set_uploads_paused(paused)
            elif path == "/api/uploads/retry":
                device.retry_now()
            elif path == "/api/reset":
                device.factory_reset()
            else:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            self._json(HTTPStatus.OK, device.status())

    return Handler


def make_server(settings, device):
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(device))
    server.daemon_threads = True
    return server
