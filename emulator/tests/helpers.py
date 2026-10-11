"""Shared set-up: settings pointing at a temp state file and a fake cloud."""
import dataclasses
import os
import tempfile
from pathlib import Path
from unittest import mock

from config import load_settings


def make_settings(cloud_url="http://127.0.0.1:9", state_dir=None, **overrides):
    # start from the real defaults, so a new setting can't be forgotten here
    with mock.patch.dict(os.environ, {}, clear=True), \
            mock.patch("config.load_dotenv"):
        settings = load_settings()
    state_dir = Path(state_dir or tempfile.mkdtemp(prefix="emulator-test-"))
    return dataclasses.replace(
        settings,
        cloud_api_url=cloud_url,
        state_file=state_dir / "device.json",
        http_timeout_seconds=2.0,
        seed=7,
        **overrides,
    )
