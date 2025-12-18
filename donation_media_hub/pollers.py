from __future__ import annotations

import threading
import time
from dataclasses import asdict
from typing import Callable, Optional

from donation_media_hub.config import POLL_INTERVAL_SEC
from donation_media_hub.services.donation_alerts import DonationAlertsClient
from donation_media_hub.services.donatex import DonateXClient


class Pollers:
    """
    Runs DA/DX polling in background threads and emits UI events via callback.
    """

    def __init__(
        self,
        emit_event: Callable[[dict], None],
        get_da_token: Callable[[], str],
        get_dx_token: Callable[[], str],
        da_last_media_id: int,
        dx_last_timestamp: Optional[float],
    ) -> None:
        self.emit_event = emit_event
        self.get_da_token = get_da_token
        self.get_dx_token = get_dx_token

        self.da_client = DonationAlertsClient(get_da_token(), da_last_media_id)
        self.dx_client = DonateXClient(get_dx_token(), dx_last_timestamp)

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop_event.clear()
        self._threads = []

        if self.get_da_token().strip():
            th = threading.Thread(target=self._loop_da, daemon=True)
            th.start()
            self._threads.append(th)

        if self.get_dx_token().strip():
            th = threading.Thread(target=self._loop_dx, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._stop_event.set()

    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    def state_snapshot(self) -> dict:
        return {
            "da_last_media_id": self.da_client.last_media_id,
            "dx_last_timestamp": self.dx_client.last_timestamp,
        }

    def _loop_da(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.da_client.token = self.get_da_token().strip()
                for t in self.da_client.fetch_new_tracks():
                    self.emit_event({"type": "new_track", "track": asdict(t)})
            except Exception as e:
                self.emit_event({"type": "log", "msg": f"❌ DA error: {e}"})
            time.sleep(POLL_INTERVAL_SEC)

    def _loop_dx(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.dx_client.token = self.get_dx_token().strip()
                for t in self.dx_client.fetch_new_tracks():
                    self.emit_event({"type": "new_track", "track": asdict(t)})
            except Exception as e:
                self.emit_event({"type": "log", "msg": f"❌ DonateX error: {e}"})
            time.sleep(POLL_INTERVAL_SEC)
