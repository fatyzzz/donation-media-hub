from __future__ import annotations

import queue as thread_queue
import shutil
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from donation_media_hub.downloader import Downloader
from donation_media_hub.models import Track
from donation_media_hub.paths import TEMP_DIR
from donation_media_hub.playback import AudioPlayer
from donation_media_hub.pollers import Pollers
from donation_media_hub.queue_manager import QueueManager
from donation_media_hub.storage import load_json, save_json


class PlayerController:
    """
    FINAL, STATE-DRIVEN CONTROLLER.

    Design guarantees:
    - Controller is the SINGLE source of truth
    - UI NEVER drives logic, only reflects state
    - Config is loaded once, written only on explicit user intent
    - No race between UI / downloader / watchdog
    """

    STATUS_ORDER = {
        "new": 0,
        "ready": 1,  # normalized to queued
        "queued": 1,
        "downloading": 2,
        "playing": 3,
        "paused": 3,
        "played": 4,
        "skipped": 4,
        "failed": 4,
    }

    # -------------------- init --------------------
    def __init__(
        self,
        queue_manager: QueueManager,
        config_file: Path,
        state_da_file: Path,
        state_dx_file: Path,
        on_ui_update,  # () -> None
        on_log,  # (str) -> None
        on_status_text,  # (str) -> None
        on_now_playing,  # (big:str, small:str) -> None
        set_current_in_view,  # (track_id:str|None) -> None
    ) -> None:
        self.queue = queue_manager
        self.config_file = config_file
        self.state_da_file = state_da_file
        self.state_dx_file = state_dx_file

        self.on_ui_update = on_ui_update
        self.on_log = on_log
        self.on_status_text = on_status_text
        self.on_now_playing = on_now_playing
        self.set_current_in_view = set_current_in_view

        self.ui_events: "thread_queue.Queue[dict]" = thread_queue.Queue()
        self._closing = False
        self._last_play_start_ts: float = 0.0

        # ---- controller-owned config cache ----
        self._da_token: str = ""
        self._dx_token: str = ""
        self._download_mode: bool = True
        self._volume: float = 0.7

        self.config_data: dict = {}
        self.da_state: dict = {}
        self.dx_state: dict = {}

        self._load_config()
        self._load_states()

        self.downloader = Downloader(TEMP_DIR)
        self.player = AudioPlayer(volume=self._volume)

        self.pollers = Pollers(
            emit_event=self.ui_events.put,
            get_da_token=lambda: self._da_token,
            get_dx_token=lambda: self._dx_token,
            da_last_media_id=int(self.da_state.get("last_media_id", 0) or 0),
            dx_last_timestamp=self.dx_state.get("last_timestamp"),
        )

        self._download_thread = threading.Thread(
            target=self._download_loop,
            daemon=True,
            name="download-loop",
        )
        self._download_thread.start()

        self._update_now_playing()

    # ======================================================================
    # CONFIG / STATE
    # ======================================================================

    def _load_config(self) -> None:
        self.config_data = load_json(
            self.config_file,
            {
                "da_token": "",
                "dx_token": "",
                "show_tokens": False,
                "download_mode": True,
                "volume": 0.7,
                "current_track_id": None,
            },
        )

        self._da_token = (self.config_data.get("da_token") or "").strip()
        self._dx_token = (self.config_data.get("dx_token") or "").strip()
        self._download_mode = bool(self.config_data.get("download_mode", True))
        self._volume = float(self.config_data.get("volume", 0.7))

        self.queue.set_current(self.config_data.get("current_track_id"))

    def save_config(
        self,
        *,
        da_token: str,
        dx_token: str,
        show_tokens: bool,
        download_mode: bool,
        volume: float,
    ) -> None:
        if self._closing:
            return

        self._da_token = (da_token or "").strip()
        self._dx_token = (dx_token or "").strip()
        self._download_mode = bool(download_mode)
        self._volume = float(volume)

        self.config_data.update(
            {
                "da_token": self._da_token,
                "dx_token": self._dx_token,
                "show_tokens": bool(show_tokens),
                "download_mode": self._download_mode,
                "volume": self._volume,
                "current_track_id": self.queue.current_track_id,
            }
        )

        save_json(self.config_file, self.config_data)

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
        try:
            self.player.set_volume(self._volume)
        except Exception:
            pass

    # -------------------- poller state --------------------

    def _load_states(self) -> None:
        self.da_state = load_json(self.state_da_file, {"last_media_id": 0})
        self.dx_state = load_json(self.state_dx_file, {"last_timestamp": None})

    def _save_states(self) -> None:
        snap = self.pollers.state_snapshot()
        self.da_state["last_media_id"] = int(snap.get("da_last_media_id", 0) or 0)
        self.dx_state["last_timestamp"] = snap.get("dx_last_timestamp")
        save_json(self.state_da_file, self.da_state)
        save_json(self.state_dx_file, self.dx_state)

    # ======================================================================
    # INTERNAL HELPERS
    # ======================================================================

    def _normalize_status(self, st: str) -> str:
        return "queued" if st == "ready" else st

    def _status_rank(self, st: str) -> int:
        return int(self.STATUS_ORDER.get(st, 0))

    def _set_current(self, track_id: Optional[str]) -> None:
        if not track_id:
            return
        self.queue.set_current(track_id)
        self.queue.save()
        self.config_data["current_track_id"] = track_id
        save_json(self.config_file, self.config_data)
        self.set_current_in_view(track_id)
        self._update_now_playing()
        self.on_ui_update()

    # ======================================================================
    # PUBLIC UI API
    # ======================================================================

    def set_current_by_id(self, track_id: str | None) -> None:
        self._set_current(track_id)

    def open_current_link(self) -> None:
        t = self.queue.current()
        if t:
            webbrowser.open(t.url)

    # ======================================================================
    # START / STOP
    # ======================================================================

    def start(self) -> None:
        if not self._da_token and not self._dx_token:
            self.on_status_text("Ошибка: нет токенов")
            self.on_log("❌ Вставь хотя бы один токен (DA или DX).")
            return

        self.pollers.da_client.token = self._da_token
        self.pollers.dx_client.token = self._dx_token

        self.pollers.start()
        self.on_status_text("Polling: ON")
        self.on_log("▶ polling started")

        if self.queue.current() and not self.player.is_playing():
            self.play_current(force=True)

    def stop(self) -> None:
        self.pollers.stop()
        self.on_status_text("Polling: OFF")
        self.on_log("⏹ polling stopped")
        self._save_states()

    # ======================================================================
    # EVENT PUMP
    # ======================================================================

    def process_ui_events(self) -> None:
        if self._closing:
            return

        dirty = False

        try:
            while True:
                ev = self.ui_events.get_nowait()
                et = ev.get("type")

                if et == "log":
                    self.on_log(str(ev.get("msg", "")))

                elif et == "new_track":
                    t = Track(**ev["track"])
                    if self.queue.append_if_new(t):
                        self.queue.sort()
                        self.on_log(f"➕ NEW [{t.source}] {t.title}")
                        if self.queue.current_track_id is None:
                            self._set_current(t.track_id)
                        dirty = True

                elif et == "track_status":
                    t = self.queue.get(ev.get("track_id"))
                    if not t:
                        continue
                    st = self._normalize_status(ev.get("status", t.status))
                    if self._status_rank(st) < self._status_rank(t.status):
                        continue
                    t.status = st
                    if ev.get("error"):
                        t.error = str(ev["error"])
                    dirty = True

                elif et == "download_done":
                    t = self.queue.get(ev.get("track_id"))
                    if not t:
                        continue
                    t.local_path = str(ev.get("path"))
                    if t.status not in {"playing", "paused"}:
                        t.status = "queued"
                    dirty = True
                    if (
                        t.track_id == self.queue.current_track_id
                        and self._download_mode
                        and not self.player.is_playing()
                    ):
                        self.play_current(force=True)

        except thread_queue.Empty:
            pass

        if dirty:
            self.queue.save()
            self._update_now_playing()
            self.on_ui_update()

    # ======================================================================
    # DOWNLOAD LOOP
    # ======================================================================

    def _download_loop(self) -> None:
        while True:
            time.sleep(0.25)

            if (
                self._closing
                or not self._download_mode
                or not self.pollers.is_running()
            ):
                continue

            cur = self.queue.current()
            if not cur:
                continue

            idx = self.queue.index_of_current()
            targets = self.queue.tracks[idx : idx + 2]

            for t in targets:
                if t.local_path and Path(t.local_path).exists():
                    continue
                if t.status in {"downloading", "playing", "paused"}:
                    continue

                self.ui_events.put(
                    {
                        "type": "track_status",
                        "track_id": t.track_id,
                        "status": "downloading",
                    }
                )
                try:
                    out = self.downloader.download_mp3(t)
                    self.ui_events.put(
                        {
                            "type": "download_done",
                            "track_id": t.track_id,
                            "path": str(out),
                        }
                    )
                    self.ui_events.put(
                        {"type": "log", "msg": f"✅ downloaded: {out.name}"}
                    )
                except Exception as e:
                    self.ui_events.put(
                        {
                            "type": "track_status",
                            "track_id": t.track_id,
                            "status": "failed",
                            "error": str(e),
                        }
                    )

    # ======================================================================
    # PLAYBACK
    # ======================================================================

    def play_current(self, *, force: bool = False) -> None:
        if self._closing:
            return

        t = self.queue.current()
        if not t:
            return

        if self.player.is_playing() or self.player.is_paused():
            return

        if not self._download_mode:
            webbrowser.open(t.url)
            t.status = "played"
            self.queue.save()
            self.next_track(auto=True)
            return

        if not self.player.is_ready():
            self.on_log("❌ pygame not ready")
            self.on_status_text("Audio error")
            return

        if not t.local_path or not Path(t.local_path).exists():
            if force:
                self.on_status_text("Waiting download…")
            t.status = "queued"
            self.queue.save()
            return

        try:
            self.player.play(t.local_path, volume=self._volume)
            self._last_play_start_ts = time.time()
            t.status = "playing"
            self.queue.save()
            self.on_status_text("Playing")
            self._cleanup_temp_window()
            self.on_ui_update()
        except Exception as e:
            t.status = "failed"
            t.error = str(e)
            self.queue.save()
            self.on_log(f"❌ play error: {e}")

    def play_pause(self) -> None:
        t = self.queue.current()
        if not t or self._closing:
            return

        if not self._download_mode:
            self.play_current(force=True)
            return

        if self.player.is_playing() and not self.player.is_paused():
            self.player.pause()
            t.status = "paused"
        elif self.player.is_paused():
            self.player.resume()
            self._last_play_start_ts = time.time()
            t.status = "playing"
        else:
            self.play_current(force=True)
            return

        self.queue.save()
        self.on_ui_update()

    # ======================================================================
    # NAVIGATION
    # ======================================================================

    def next_track(self, *, auto: bool) -> None:
        if self._closing:
            return

        cur = self.queue.current()
        if cur:
            cur.status = "played" if auto else "skipped"

        self.player.stop()

        nid = self.queue.next_id()
        if not nid:
            self.on_status_text("End of queue")
            return

        self._set_current(nid)
        self.play_current(force=True)

    def prev_track(self) -> None:
        pid = self.queue.prev_id()
        if pid:
            self.player.stop()
            self._set_current(pid)
            self.play_current(force=True)

    def skip_track(self) -> None:
        cur = self.queue.current()
        if cur:
            cur.status = "skipped"
            self.queue.save()
            self.player.stop()
            self.next_track(auto=False)

    def go_start(self) -> None:
        if self.queue.tracks:
            self.player.stop()
            self._set_current(self.queue.tracks[0].track_id)
            self.play_current(force=True)

    # ======================================================================
    # WATCHDOG
    # ======================================================================

    def watchdog(self) -> None:
        if self._closing:
            return

        if not self.pollers.is_running() or not self._download_mode:
            return

        if self.player.is_paused():
            return

        if (time.time() - self._last_play_start_ts) < 1.2:
            return

        if not self.player.is_playing():
            cur = self.queue.current()
            if cur and cur.status == "playing":
                cur.status = "played"
                self.queue.save()
                self._cleanup_temp_window()
                self.next_track(auto=True)

    # ======================================================================
    # CLEANUP
    # ======================================================================

    def _cleanup_temp_window(self) -> None:
        idx = self.queue.index_of_current()
        if idx < 0:
            return

        keep_ids = {
            t.track_id
            for t in self.queue.tracks[max(0, idx - 1) : idx + 2]
            if t.local_path
        }

        keep_paths = {
            str(Path(t.local_path).resolve())
            for t in self.queue.tracks
            if t.track_id in keep_ids and t.local_path
        }

        Downloader.cleanup_keep(TEMP_DIR, keep_paths)

        for t in self.queue.tracks:
            if t.local_path and not Path(t.local_path).exists():
                t.local_path = None

        self.queue.save()

    def clear_temp(self) -> None:
        self.player.stop()
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        for t in self.queue.tracks:
            t.local_path = None
            if t.status in {"downloading", "playing", "paused"}:
                t.status = "queued"

        self.queue.save()
        self.on_log("🧹 temp cleared")
        self.on_ui_update()

    def clear_queue(self) -> None:
        self.player.stop()
        self.queue.clear()
        self.queue.set_current(None)
        self.queue.save()
        self.config_data["current_track_id"] = None
        save_json(self.config_file, self.config_data)
        self.on_log("🗑 queue cleared")
        self.on_status_text("Queue cleared")
        self._update_now_playing()
        self.on_ui_update()

    # ======================================================================
    # NOW PLAYING
    # ======================================================================

    def _update_now_playing(self) -> None:
        t = self.queue.current()
        if not t:
            self.on_now_playing("—", "Queue empty")
            return

        extra = f"Status: {t.status}"
        if t.local_path and Path(t.local_path).exists():
            extra += f" · {Path(t.local_path).name}"

        self.on_now_playing(f"[{t.source}] {t.title}", extra)

    # ======================================================================
    # CLOSE
    # ======================================================================

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.pollers.stop()
        self._save_states()
        self.queue.save()
        try:
            self.player.shutdown()
        except Exception:
            pass
