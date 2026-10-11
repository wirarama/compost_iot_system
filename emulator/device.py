"""
The device itself: sensors, pairing and the uplink to the cloud.

Two background loops run for as long as the device is powered on:

  sampler   every READING_INTERVAL_SECONDS, advance the pile by
            READING_INTERVAL_SECONDS x TIME_SCALE of compost time and take a
            reading. Readings are stamped with the real wall-clock time, as a
            real device with NTP would, so only the pile runs fast.
  uploader  sends queued readings to the cloud in batches. If the cloud is
            down it backs off and keeps the readings (up to QUEUE_MAX, oldest
            dropped first) until it comes back.

Pairing gives the device its key, but the cloud only takes its readings once
the user has confirmed the registration in the dashboard and given the device
a name and a bin. Until then /records answers 409, and the device waits in
"awaiting_setup": it checks back every SETUP_CHECK_SECONDS and holds only its
newest reading, so readings from before it was set up never land in a bin.

The web page (web.py) only ever calls the public methods here.
"""
import itertools
import logging
import random
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

from cloud import CloudClient, CloudError, CloudUnreachable
from model import ACTIONS, CompostModel
from state import Credentials, StateFile, fake_mac

log = logging.getLogger(__name__)

HISTORY_LENGTH = 120
EVENT_LOG_LENGTH = 20
BACKOFF_FIRST_SECONDS = 5
BACKOFF_MAX_SECONDS = 300
CODE_PATTERN = re.compile(r"^\d{6}$")
AWAITING_SETUP = ("Paired. Finish setting up this device in the dashboard (confirm the "
                  "registration, then give it a name and a bin) and readings will start.")

# sensor names on the device -> field names the API's /records expects
API_FIELDS = {
    "temperature_c": "temperature",
    "moisture_pct": "moisture_percent",
    "o2_pct": "o2_percent",
    "co2_pct": "co2_percent",
    "nh3_relative": "nh3_ratio",
}

PAIRING_ERRORS = {
    404: "That code wasn't recognised, or it has expired. Get a new code from the dashboard.",
    409: "That code has already been used. Get a new code from the dashboard.",
    410: "That code has expired. Get a new code from the dashboard.",
    429: "Too many attempts. Wait a few minutes, then try again.",
}


class PairingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def to_api_record(reading):
    record = {"timestamp": reading["timestamp"]}
    for field, api_field in API_FIELDS.items():
        record[api_field] = reading[field]
    return record


class Device:
    def __init__(self, settings, cloud=None):
        self.settings = settings
        self.cloud = cloud or CloudClient(settings)
        self.lock = threading.RLock()
        self.pairing_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_uploader = threading.Event()
        self.threads = []
        self.rng = random.Random(settings.seed)

        self.store = StateFile(settings.state_file)
        self.store.load()
        self.store.device_uid = settings.device_uid or self.store.device_uid or fake_mac()
        self.model = self._restore_model()
        self.store.model = self.model.to_dict()
        self.store.save()

        self.history = deque(maxlen=HISTORY_LENGTH)
        self.events = deque(maxlen=EVENT_LOG_LENGTH)
        self.latest = None

        # (seq, record) pairs, so a successful upload removes exactly what it
        # sent even if the queue overflowed while the request was in flight
        self.queue = deque()
        self.seq = 0
        # bumped whenever the credentials change, so an upload that was in
        # flight during an unpair can't act on the old key's result
        self.generation = 0

        self.uploads_paused = False
        self.retry_at = 0.0
        self.failures = 0
        self.uplink = {
            "state": "idle",
            "message": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "sent_total": 0,
            "dropped_total": 0,
        }
        self._log_event("power on")

    def _restore_model(self):
        if self.store.model:
            try:
                return CompostModel.from_dict(self.store.model, self.rng, self.settings.ambient_temp_c)
            except (KeyError, TypeError, ValueError) as error:
                log.warning("saved pile state is unusable (%s); starting a new batch", error)
        return CompostModel(self.rng, self.settings.ambient_temp_c, self.settings.start_stage)

    # ----------------------------------------------------------- lifecycle ---

    def start(self):
        with self.lock:
            self._take_reading()
        for name, target in (("sampler", self._sampler_loop), ("uploader", self._uploader_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        self.wake_uploader.set()
        for thread in self.threads:
            thread.join(timeout=self.settings.http_timeout_seconds + 1)
        with self.lock:
            self._save()

    def _save(self):
        self.store.model = self.model.to_dict()
        try:
            self.store.save()
        except OSError as error:
            log.error("couldn't save state to %s: %s", self.store.path, error)

    def _log_event(self, text):
        self.events.appendleft({"at": utc_now(), "text": text})
        log.info(text)

    # ------------------------------------------------------------- pairing ---

    @property
    def credentials(self):
        return self.store.credentials

    def _pairing_state(self):
        creds = self.credentials
        if creds is None:
            return "unpaired"
        if creds.cloud_api_url != self.settings.cloud_api_url:
            return "other_cloud"
        return "paired"

    def pair(self, code):
        code = (code or "").strip().replace(" ", "")
        if not CODE_PATTERN.match(code):
            raise PairingError("The pairing code is the 6 digits shown in the dashboard.")
        with self.lock:
            if self.credentials is not None:
                raise PairingError("This device is already paired. Factory reset it to pair again.", 409)
        if not self.pairing_lock.acquire(blocking=False):
            raise PairingError("Pairing is already in progress.", 409)
        try:
            api_key, device_id = self.cloud.redeem_code(code, self.store.device_uid)
        except CloudUnreachable as error:
            raise PairingError(f"Couldn't reach the cloud at {self.settings.cloud_api_url} ({error}).", 502)
        except CloudError as error:
            message = PAIRING_ERRORS.get(error.status, f"The cloud refused the code: {error.detail}")
            raise PairingError(message, 400 if error.status < 500 else 502)
        finally:
            self.pairing_lock.release()

        with self.lock:
            self.store.credentials = Credentials(
                device_id=device_id,
                api_key=api_key,
                paired_at=utc_now(),
                cloud_api_url=self.settings.cloud_api_url,
            )
            self.generation += 1
            self.queue.clear()
            self.failures = 0
            self.retry_at = 0.0
            self._set_uplink("awaiting_setup", AWAITING_SETUP)
            self._save()
            self._log_event(f"paired as device {device_id or '(id not given)'}; "
                            "waiting for setup in the dashboard")
        self.wake_uploader.set()

    def _forget_credentials(self, reason):
        self.store.credentials = None
        self.generation += 1
        self.queue.clear()
        self.failures = 0
        self.retry_at = 0.0
        self._save()
        self._log_event(reason)

    def factory_reset(self):
        # wipes pairing, not the hardware ID or the compost in the bin
        with self.lock:
            self._forget_credentials("factory reset: pairing cleared")
            self._set_uplink("idle", None)

    # --------------------------------------------------------------- pile ---

    def apply_action(self, action):
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        labels = {"turn": "pile turned", "water": "water added", "feedstock": "fresh feedstock added"}
        with self.lock:
            self.model.apply(action)
            self._log_event(labels[action])
            # read straight away, so the effect shows without waiting a cycle
            self._take_reading()
        self.wake_uploader.set()

    def _sampler_loop(self):
        interval = self.settings.reading_interval_seconds
        dt_days = interval * self.settings.time_scale / 86400
        while not self.stop_event.wait(interval):
            with self.lock:
                self.model.step(dt_days)
                self._take_reading()
            self.wake_uploader.set()

    def _take_reading(self):
        reading = {"timestamp": utc_now(), **self.model.read()}
        self.latest = reading
        self.history.append(reading)
        # an unpaired device has nowhere to send readings, so it doesn't keep them
        if self._pairing_state() == "paired":
            self.seq += 1
            self.queue.append((self.seq, to_api_record(reading)))
            # not set up yet: only the newest reading is worth sending later
            limit = 1 if self.uplink["state"] == "awaiting_setup" else self.settings.queue_max
            while len(self.queue) > limit:
                self.queue.popleft()
                if limit > 1:
                    self.uplink["dropped_total"] += 1
        self._save()

    # -------------------------------------------------------------- uplink ---

    def set_uploads_paused(self, paused):
        with self.lock:
            self.uploads_paused = bool(paused)
            self._log_event("uploads paused" if paused else "uploads resumed")
        self.wake_uploader.set()

    def retry_now(self):
        with self.lock:
            self.retry_at = 0.0
        self.wake_uploader.set()

    def _set_uplink(self, state, message):
        self.uplink["state"] = state
        self.uplink["message"] = message

    def _back_off(self, state, message):
        self.failures += 1
        delay = min(BACKOFF_MAX_SECONDS, BACKOFF_FIRST_SECONDS * 2 ** (self.failures - 1))
        self.retry_at = time.monotonic() + delay
        self._set_uplink(state, f"{message} Retrying in {delay} s.")

    def _uploader_loop(self):
        while not self.stop_event.is_set():
            # the timeout also lets a finished back-off be noticed
            self.wake_uploader.wait(timeout=1.0)
            self.wake_uploader.clear()
            if not self.stop_event.is_set():
                self._upload_once()

    def _upload_once(self):
        with self.lock:
            if self.uploads_paused or self._pairing_state() != "paired" or not self.queue:
                return
            if time.monotonic() < self.retry_at:
                return
            batch = list(itertools.islice(self.queue, self.settings.upload_batch_max))
            api_key = self.credentials.api_key
            generation = self.generation
            self.uplink["last_attempt_at"] = utc_now()

        try:
            self.cloud.send_records(api_key, [record for _, record in batch])
        except CloudUnreachable as error:
            with self.lock:
                if generation == self.generation:
                    self._back_off("offline", f"Can't reach {self.settings.cloud_api_url} ({error}).")
            return
        except CloudError as error:
            with self.lock:
                if generation == self.generation:
                    self._handle_rejection(error, batch)
            return

        with self.lock:
            if generation != self.generation:
                return
            last_sent = batch[-1][0]
            while self.queue and self.queue[0][0] <= last_sent:
                self.queue.popleft()
            self.failures = 0
            self.retry_at = 0.0
            self.uplink["sent_total"] += len(batch)
            self.uplink["last_success_at"] = utc_now()
            if self.uplink["state"] == "awaiting_setup":
                self._log_event("setup finished in the dashboard; the cloud is taking readings")
            self._set_uplink("ok", f"Sent {len(batch)} reading{'s' if len(batch) != 1 else ''}.")
            more = bool(self.queue)
        if more:
            self.wake_uploader.set()

    def _handle_rejection(self, error, batch):
        if error.status in (401, 403):
            # the key was revoked, most likely by removing the device in the dashboard
            self._forget_credentials("the cloud rejected this device's key; pairing cleared")
            self._set_uplink("revoked", "The cloud no longer accepts this device's key "
                                        "(it was probably removed in the dashboard). Pair it again.")
        elif error.status == 409:
            # paired but not set up (or moved out of its bin): expected, not a
            # fault, so check back at a steady pace instead of backing off
            if self.uplink["state"] != "awaiting_setup":
                self._log_event("the cloud has no bin for this device; waiting for setup in the dashboard")
            while len(self.queue) > 1:
                self.queue.popleft()
            self.failures = 0
            self.retry_at = time.monotonic() + self.settings.setup_check_seconds
            self._set_uplink("awaiting_setup", AWAITING_SETUP)
        elif error.status in (400, 413, 422):
            # resending the same batch can never work, so drop it rather than wedge the queue
            last_sent = batch[-1][0]
            while self.queue and self.queue[0][0] <= last_sent:
                self.queue.popleft()
            self.uplink["dropped_total"] += len(batch)
            log.error("cloud rejected a batch of %d readings: %s", len(batch), error)
            self._set_uplink("rejected", f"The cloud rejected {len(batch)} readings ({error.status}: "
                                         f"{error.detail}); they were dropped.")
        else:
            self._back_off("error", f"The cloud returned {error.status}: {error.detail}.")

    # -------------------------------------------------------------- status ---

    def status(self):
        with self.lock:
            creds = self.credentials
            pairing = {"state": self._pairing_state()}
            if creds:
                pairing.update({
                    "device_id": creds.device_id,
                    "paired_at": creds.paired_at,
                    "cloud_api_url": creds.cloud_api_url,
                    "api_key_hint": "••••" + creds.api_key[-4:],
                })
            retry_in = max(0, round(self.retry_at - time.monotonic()))
            return {
                "device": {
                    "uid": self.store.device_uid,
                    "model": self.settings.device_model,
                    "firmware_version": self.settings.firmware_version,
                },
                "pairing": pairing,
                "cloud": {
                    "api_url": self.settings.cloud_api_url,
                    "pairing_url": self.settings.cloud_url(self.settings.pairing_path),
                    "records_url": self.settings.cloud_url(self.settings.records_path),
                    "paused": self.uploads_paused,
                    "queued": len(self.queue),
                    "retry_in_seconds": retry_in,
                    **self.uplink,
                },
                "sampling": {
                    "reading_interval_seconds": self.settings.reading_interval_seconds,
                    "time_scale": self.settings.time_scale,
                },
                "pile": self.model.summary(),
                "latest": self.latest,
                "history": list(self.history),
                "events": list(self.events),
            }
