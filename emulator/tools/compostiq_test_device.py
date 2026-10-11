#!/usr/bin/env python3
"""
Be a CompostIQ device by hand: pair with a code from the dashboard, then send
test readings to the cloud API.

Python 3.8 or newer, nothing to install (standard library only).

1. Pair, once. In the dashboard go to Devices -> Add device -> Pair and copy
   the 6-digit code, then:

       python compostiq_test_device.py pair 123456

   The API hands the key over only once. It's saved to
   compostiq_test_device.json next to this script, which the commands below
   read. Keep that file private: anyone with the key can send readings.

2. Finish setup in the dashboard: check the hardware ID matches the one this
   script printed, press Confirm registration, and give the device a name and
   a bin. Until then the API answers 409 and stores nothing.

3. Send readings:

       python compostiq_test_device.py check                     # is the key good? stores nothing
       python compostiq_test_device.py send                      # 6 healthy readings, 5 min apart, ending now
       python compostiq_test_device.py send --scenario too_hot   # opens a "too hot" alert
       python compostiq_test_device.py live --every 30           # one reading every 30 s until Ctrl+C

Already have a key, e.g. from the emulator? Skip step 1 and put it in the
environment: export COMPOSTIQ_API_KEY=<64 hex characters> (on Windows
PowerShell: $env:COMPOSTIQ_API_KEY="..."). With neither that nor a saved file,
the script asks for the key without echoing it. Don't pass the key as a
command-line argument: it would end up in your shell history.

Testing against your own API instead of production: --url http://127.0.0.1:8000
(or set COMPOSTIQ_API_URL).

Alerts: a reading over 70 C opens "too hot", moisture under 40 % opens
"too dry", and over 60 % opens "too wet". Healthy readings afterwards resolve
them. Only readings newer than the bin's latest stored reading count towards
alerts, so backdated readings are stored but never open or close one.
"""
import argparse
import datetime
import getpass
import json
import os
import random
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://api.compostiq.win"
SAVED_FILE = Path(__file__).resolve().with_name("compostiq_test_device.json")
DEVICE_MODEL = "CompostIQ Manual Test"
FIRMWARE_VERSION = "1.0"
TIMEOUT_SECONDS = 15

# (low, high) for each value; an alert scenario pushes one of them out of range
HEALTHY = {
    "temperature": (55.0, 62.0),
    "moisture_percent": (50.0, 58.0),
    "o2_percent": (8.0, 12.0),
    "co2_percent": (5.0, 9.0),
    "nh3_ratio": (0.80, 1.00),
}
SCENARIOS = {
    "healthy": {},
    "too_hot": {"temperature": (71.0, 75.0)},
    "too_dry": {"moisture_percent": (32.0, 38.0)},
    "too_wet": {"moisture_percent": (63.0, 68.0)},
}

# what the API's answers mean, in words a tester can act on
PAIRING_ANSWERS = {
    404: "That code wasn't recognised, or it has expired. Make a new one in the dashboard.",
    409: "That code has already been used, or this device is being paired right now.",
    410: "That code has expired. Make a new one in the dashboard.",
    422: "The code must be 6 digits, and --device-uid must look like 02:1A:9C:44:E0:7B.",
    429: "Too many attempts. Wait a few minutes, then try again.",
}
RECORDS_ANSWERS = {
    401: "The API key was rejected: it's wrong, or the device was removed in the dashboard.",
    403: "The API key was rejected: it's wrong, or the device was removed in the dashboard.",
    409: "The key is good, but the device isn't set up yet. In the dashboard, press "
         "Confirm registration and give it a name and a bin.",
    422: "The API didn't accept the readings.",
}
# answers that won't change by trying again, so `live` stops on them
FATAL = {401, 403, 422}


def post(url, payload, headers=None):
    """POST JSON and return (status, body); exits if the API can't be reached."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"{DEVICE_MODEL}/{FIRMWARE_VERSION}",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, parse(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, parse(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        sys.exit(f"Couldn't reach {url}: {getattr(error, 'reason', error)}")


def parse(raw):
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text


def explain(status, body, answers):
    print(f"HTTP {status}: {answers.get(status, 'Unexpected answer from the API.')}")
    detail = body.get("detail") if isinstance(body, dict) else body
    if detail:
        print(f"  The API said: {detail if isinstance(detail, str) else json.dumps(detail)}")


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def fake_mac():
    # 0x02 in the first byte marks a locally administered address, so it can
    # never clash with a real manufacturer's MAC
    octets = [0x02] + [secrets.randbelow(256) for _ in range(5)]
    return ":".join(f"{octet:02X}" for octet in octets)


def make_reading(scenario, at):
    ranges = {**HEALTHY, **SCENARIOS[scenario]}
    reading = {"timestamp": at.isoformat()}
    for name, (low, high) in ranges.items():
        reading[name] = round(random.uniform(low, high), 2 if name == "nh3_ratio" else 1)
    return reading


def show(readings):
    print(f"{'timestamp (UTC)':<27}{'temp C':>7}{'moist %':>9}{'O2 %':>7}{'CO2 %':>7}{'NH3':>6}")
    for r in readings:
        print(f"{r['timestamp']:<27}{r['temperature']:>7}{r['moisture_percent']:>9}"
              f"{r['o2_percent']:>7}{r['co2_percent']:>7}{r['nh3_ratio']:>6}")


def load_saved():
    if not SAVED_FILE.is_file():
        return None
    try:
        return json.loads(SAVED_FILE.read_text())
    except ValueError:
        sys.exit(f"{SAVED_FILE} is unreadable. Delete it and pair again.")


def save(data):
    # readable by its owner only, because it holds the API key
    fd = os.open(SAVED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def api_url(args, saved=None):
    url = args.url or os.environ.get("COMPOSTIQ_API_URL") or (saved or {}).get("api_url") or DEFAULT_URL
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        sys.exit(f"The API URL must start with http:// or https://, got {url!r}")
    return url


def credentials(args):
    """The API key to use, and the API to send it to."""
    key = os.environ.get("COMPOSTIQ_API_KEY", "").strip()
    if key:
        return key, api_url(args)
    saved = load_saved()
    if saved:
        url = api_url(args, saved)
        # the key only works on the API that issued it; never send it anywhere else
        if url != saved["api_url"]:
            sys.exit(f"The saved key was issued by {saved['api_url']}, not {url}. "
                     "Pair again against that API, or set COMPOSTIQ_API_KEY.")
        return saved["api_key"], url
    key = getpass.getpass("Device API key (typing is hidden): ").strip()
    if not key:
        sys.exit("No API key given. Pair first, or set COMPOSTIQ_API_KEY.")
    return key, api_url(args)


def send(url, key, readings):
    status, body = post(url + "/records", readings, {"x-key": key})
    if 200 <= status < 300:
        return status
    explain(status, body, RECORDS_ANSWERS)
    return status


def cmd_pair(args):
    saved = load_saved()
    url = api_url(args, saved)
    # re-pairing keeps the same hardware ID, as a real device would after a reset
    device_uid = args.device_uid or (saved or {}).get("device_uid") or fake_mac()
    status, body = post(url + "/pairing/redeem", {
        "code": args.code.strip(),
        "device_uid": device_uid,
        "model": DEVICE_MODEL,
        "firmware_version": FIRMWARE_VERSION,
    })
    if status not in (200, 201):
        explain(status, body, PAIRING_ANSWERS)
        return 1
    save({
        "device_uid": device_uid,
        "device_id": body.get("device_id"),
        "api_key": body["api_key"],
        "api_url": url,
        "paired_at": now().isoformat(),
    })
    print(f"Paired with {url}")
    print(f"  Hardware ID: {device_uid}   <- check the dashboard shows this one")
    print(f"  Device ID:   {body.get('device_id')}")
    print(f"  Key saved to {SAVED_FILE}")
    print("Next: in the dashboard, press Confirm registration and give the device a name and a bin.")
    print("Then run:  python compostiq_test_device.py check")
    return 0


def cmd_check(args):
    key, url = credentials(args)
    # an empty batch authenticates and checks the bin, but stores nothing
    status = send(url, key, [])
    if 200 <= status < 300:
        print(f"The key works and the device is set up with a bin on {url}. Nothing was stored.")
        return 0
    return 1


def cmd_send(args):
    key, url = credentials(args)
    end = now()
    step = datetime.timedelta(minutes=args.spacing)
    readings = [make_reading(args.scenario, end - step * (args.count - 1 - i)) for i in range(args.count)]
    status = send(url, key, readings)
    if 200 <= status < 300:
        print(f"Stored {len(readings)} {args.scenario} readings on {url}:")
        show(readings)
        return 0
    return 1


def cmd_live(args):
    key, url = credentials(args)
    print(f"Sending one {args.scenario} reading to {url} every {args.every:g} s. Ctrl+C to stop.")
    sent = 0
    try:
        while True:
            reading = make_reading(args.scenario, now())
            status = send(url, key, [reading])
            if 200 <= status < 300:
                sent += 1
                print(f"{reading['timestamp']}  {reading['temperature']} C  "
                      f"{reading['moisture_percent']} %  sent ({sent} so far)")
            elif status in FATAL:
                return 1
            time.sleep(args.every)
    except KeyboardInterrupt:
        print(f"\nStopped after {sent} readings.")
    return 0


def at_least(minimum, cast):
    def check(text):
        value = cast(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}")
        return value
    return check


def main():
    parser = argparse.ArgumentParser(
        description="Pair a test device with CompostIQ and send it fake readings.",
        epilog="Full instructions are at the top of this file.",
    )
    parser.add_argument("--url", help=f"the API to talk to (default {DEFAULT_URL}, or COMPOSTIQ_API_URL)")
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser("pair", help="redeem a 6-digit code from the dashboard for an API key")
    pair.add_argument("code", help="the 6-digit pairing code shown in the dashboard")
    pair.add_argument("--device-uid", help="hardware ID (MAC) to pair as; default: the saved one, or a new random one")
    pair.set_defaults(run=cmd_pair)

    check = commands.add_parser("check", help="test the API key without storing anything")
    check.set_defaults(run=cmd_check)

    batch = commands.add_parser("send", help="send a batch of readings, spaced out and ending now")
    batch.add_argument("--count", type=at_least(1, int), default=6, help="how many readings (default 6)")
    batch.add_argument("--spacing", type=at_least(0.1, float), default=5, help="minutes between readings (default 5)")
    batch.add_argument("--scenario", choices=SCENARIOS, default="healthy")
    batch.set_defaults(run=cmd_send)

    live = commands.add_parser("live", help="keep sending one reading at a time, like a real device")
    live.add_argument("--every", type=at_least(1, float), default=30, help="seconds between readings (default 30)")
    live.add_argument("--scenario", choices=SCENARIOS, default="healthy")
    live.set_defaults(run=cmd_live)

    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
