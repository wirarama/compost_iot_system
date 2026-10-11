"""
Power on the emulated CompostIQ device.

    python emulator/main.py            # from a checkout
    docker compose up                  # from emulator/, see README.md
"""
import logging
import signal

from config import load_settings_or_exit
from device import Device
from web import make_server

log = logging.getLogger("emulator")


def main():
    settings = load_settings_or_exit()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    device = Device(settings)
    server = make_server(settings, device)

    # docker stop sends SIGTERM; turn it into the same clean shutdown as Ctrl+C
    def on_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_sigterm)

    device.start()
    log.info("device %s up on http://%s:%d, cloud %s, one reading every %gs at %gx compost time",
             device.store.device_uid, settings.host, settings.port, settings.cloud_api_url,
             settings.reading_interval_seconds, settings.time_scale)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("shutting down")
        server.server_close()
        device.stop()


if __name__ == "__main__":
    main()
