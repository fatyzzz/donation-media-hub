from __future__ import annotations

from donation_media_hub.paths import CONFIG_FILE, QUEUE_FILE, STATE_DA_FILE, STATE_DX_FILE
from donation_media_hub.queue_manager import QueueManager
from donation_media_hub.ui.main_window import MainWindow


def main() -> None:
    queue = QueueManager(QUEUE_FILE)
    queue.load()

    app = MainWindow(
        queue_manager=queue,
        config_file=CONFIG_FILE,
        state_da_file=STATE_DA_FILE,
        state_dx_file=STATE_DX_FILE,
    )
    app.mainloop()
