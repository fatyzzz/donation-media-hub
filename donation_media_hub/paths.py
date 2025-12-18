from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def get_app_dir() -> Path:
    """
    Returns directory for config/state files.

    - In dev: repo root / donation_media_hub package parent (run.py location)
    - In PyInstaller: executable directory
    """
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


APP_DIR = get_app_dir()

CONFIG_FILE = APP_DIR / "config.json"
STATE_DA_FILE = APP_DIR / "state_donationalerts.json"
STATE_DX_FILE = APP_DIR / "state_donatex.json"
QUEUE_FILE = APP_DIR / "queue.json"

TEMP_DIR = Path(tempfile.gettempdir()) / "donation_media_hub_tracks"
