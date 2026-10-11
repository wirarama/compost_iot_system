"""
Device settings, read once at start-up from environment variables.

Every knob has a default, so the emulator runs with no configuration at all
and talks to the production cloud. For local development, point
CLOUD_API_URL at your own API (see .env.example). A .env file next to this
module is loaded too, but never overrides a variable that is already set, so
Docker's `environment:` and `env_file:` always win.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

THIS_FOLDER = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # where the cloud API lives, and the two routes the device calls on it
    cloud_api_url: str
    pairing_path: str
    records_path: str
    http_timeout_seconds: float

    # the device's own web page
    host: str
    port: int

    # identity + credentials + pile state survive restarts in this file
    state_file: Path

    # how the pile is simulated
    reading_interval_seconds: float
    time_scale: float
    start_stage: int
    ambient_temp_c: float
    seed: int | None

    # uplink behaviour
    upload_batch_max: int
    queue_max: int
    setup_check_seconds: float

    # what the device says it is
    device_uid: str | None
    device_model: str
    firmware_version: str

    log_level: str

    def cloud_url(self, path):
        return self.cloud_api_url + path


def load_dotenv(path):
    # tiny KEY=VALUE reader, so the device needs no packages at all
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class ConfigError(ValueError):
    pass


def _get(name, default):
    value = os.environ.get(name, "").strip()
    return value if value else default


def _number(name, default, cast, minimum=None, maximum=None):
    raw = _get(name, None)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be at most {maximum}, got {value}")
    return value


def _path(name, default):
    # "/pairing/redeem" and "pairing/redeem" both work
    value = _get(name, default)
    return "/" + value.lstrip("/")


def load_settings():
    load_dotenv(THIS_FOLDER / ".env")

    cloud_api_url = _get("CLOUD_API_URL", "https://api.compostiq.win").rstrip("/")
    if not cloud_api_url.startswith(("http://", "https://")):
        raise ConfigError(f"CLOUD_API_URL must start with http:// or https://, got {cloud_api_url!r}")

    return Settings(
        cloud_api_url=cloud_api_url,
        pairing_path=_path("PAIRING_PATH", "/pairing/redeem"),
        records_path=_path("RECORDS_PATH", "/records"),
        http_timeout_seconds=_number("HTTP_TIMEOUT_SECONDS", 10.0, float, minimum=1),
        host=_get("DEVICE_HOST", "127.0.0.1"),
        port=_number("DEVICE_PORT", 8080, int, minimum=1, maximum=65535),
        state_file=Path(_get("STATE_FILE", str(THIS_FOLDER / "state" / "device.json"))),
        reading_interval_seconds=_number("READING_INTERVAL_SECONDS", 30.0, float, minimum=1),
        time_scale=_number("TIME_SCALE", 60.0, float, minimum=1),
        start_stage=_number("START_STAGE", 0, int, minimum=0, maximum=4),
        ambient_temp_c=_number("AMBIENT_TEMP_C", 28.0, float, minimum=-10, maximum=45),
        seed=_number("SEED", None, int),
        upload_batch_max=_number("UPLOAD_BATCH_MAX", 50, int, minimum=1, maximum=1000),
        queue_max=_number("QUEUE_MAX", 1000, int, minimum=1),
        setup_check_seconds=_number("SETUP_CHECK_SECONDS", 10.0, float, minimum=1),
        device_uid=_get("DEVICE_UID", None),
        device_model=_get("DEVICE_MODEL", "CompostIQ Emulator"),
        firmware_version=_get("FIRMWARE_VERSION", "0.1.0"),
        log_level=_get("LOG_LEVEL", "INFO").upper(),
    )


def load_settings_or_exit():
    try:
        return load_settings()
    except ConfigError as error:
        sys.exit(f"config error: {error}")
