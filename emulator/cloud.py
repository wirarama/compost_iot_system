"""
The device's only connection to the outside world: two HTTPS calls.

  POST {CLOUD_API_URL}{PAIRING_PATH}   redeem a pairing code for an API key
  POST {CLOUD_API_URL}{RECORDS_PATH}   send readings, authenticated by x-key

Both use the standard library's urllib, the same "one request, one JSON
reply" shape an ESP32's HTTPClient would make.
"""
import json
import urllib.error
import urllib.request


class CloudError(Exception):
    """The cloud answered, but said no."""

    def __init__(self, status, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class CloudUnreachable(Exception):
    """No answer at all: DNS, refused connection, TLS failure or timeout."""


def _detail(body, status):
    # FastAPI puts its reason in "detail"; fall back to the raw text
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # 422 validation errors
        detail = "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail or body.strip()[:200] or f"HTTP {status}")


class CloudClient:
    def __init__(self, settings):
        self.settings = settings

    def _post(self, path, payload, headers=None):
        request = urllib.request.Request(
            self.settings.cloud_url(path),
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"{self.settings.device_model}/{self.settings.firmware_version}",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.http_timeout_seconds) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            with error:
                body = error.read().decode("utf-8", "replace")
            raise CloudError(error.code, _detail(body, error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise CloudUnreachable(str(reason)) from None
        except ValueError:
            raise CloudError(502, "the cloud sent back something that isn't JSON") from None

    def redeem_code(self, code, device_uid):
        """Swap a pairing code from the dashboard for this device's API key."""
        reply = self._post(self.settings.pairing_path, {
            "code": code,
            "device_uid": device_uid,
            "model": self.settings.device_model,
            "firmware_version": self.settings.firmware_version,
        })
        api_key = reply.get("api_key") if isinstance(reply, dict) else None
        if not isinstance(api_key, str) or not api_key:
            raise CloudError(502, "pairing reply had no api_key")
        device_id = reply.get("device_id")
        return api_key, str(device_id) if device_id is not None else None

    def send_records(self, api_key, records):
        self._post(self.settings.records_path, records, headers={"x-key": api_key})
