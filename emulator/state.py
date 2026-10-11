"""
The device's flash storage: one small JSON file that survives restarts.

It holds the device's hardware identity, its cloud credentials once it's
paired, and the state of the pile. On a real ESP32 this would be NVS; here
it's a file that is written atomically (temp file + rename), so a crash
mid-write never leaves half a file behind, and readable only by its owner
because it contains the API key.
"""
import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)


@dataclass
class Credentials:
    device_id: str | None
    api_key: str
    paired_at: str
    # the key only works on the cloud that issued it; remembering which one
    # stops the device sending it anywhere else if CLOUD_API_URL changes
    cloud_api_url: str


def fake_mac(rng=secrets):
    # 0x02 in the first byte marks a locally administered, unicast address,
    # so it can never collide with a real manufacturer's MAC
    octets = [0x02] + [rng.randbelow(256) for _ in range(5)]
    return ":".join(f"{octet:02X}" for octet in octets)


class StateFile:
    def __init__(self, path):
        self.path = path
        self.device_uid = None
        self.credentials = None
        self.model = None

    def load(self):
        if not self.path.is_file():
            return False
        try:
            saved = json.loads(self.path.read_text())
            self.device_uid = saved.get("device_uid")
            creds = saved.get("credentials")
            self.credentials = Credentials(**creds) if creds else None
            self.model = saved.get("model")
        except (OSError, ValueError, TypeError) as error:
            # a corrupt file is treated like a factory-fresh device, but the
            # old file is kept aside so nothing is silently lost
            broken = self.path.with_suffix(".corrupt")
            log.error("state file %s is unreadable (%s); moved to %s", self.path, error, broken)
            os.replace(self.path, broken)
            self.device_uid = self.credentials = self.model = None
            return False
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "device_uid": self.device_uid,
            "credentials": asdict(self.credentials) if self.credentials else None,
            "model": self.model,
        }
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)
