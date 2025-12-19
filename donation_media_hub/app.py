# donation_media_hub/app.py
from __future__ import annotations

from donation_media_hub.paths import (
    CONFIG_FILE,
    QUEUE_FILE,
    STATE_DA_FILE,
    STATE_DX_FILE,
)
from donation_media_hub.queue_manager import QueueManager
from donation_media_hub.ui.main_window import run_qt


def main() -> None:
    queue = QueueManager(QUEUE_FILE)
    queue.load()
    run_qt(queue, CONFIG_FILE, STATE_DA_FILE, STATE_DX_FILE)
