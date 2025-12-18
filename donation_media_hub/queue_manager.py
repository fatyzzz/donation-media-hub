from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from donation_media_hub.config import QUEUE_LIMIT
from donation_media_hub.models import Track
from donation_media_hub.storage import load_json, save_json


class QueueManager:
    def __init__(self, queue_file: Path) -> None:
        self._queue_file = queue_file
        self.tracks: List[Track] = []
        self.current_track_id: Optional[str] = None

    def load(self) -> None:
        data = load_json(self._queue_file, {"tracks": [], "current_track_id": None})
        self.tracks = []
        for raw in data.get("tracks", []):
            try:
                self.tracks.append(Track(**raw))
            except Exception:
                continue
        self.current_track_id = data.get("current_track_id")
        self.sort()
        self._ensure_current()

    def save(self) -> None:
        save_json(
            self._queue_file,
            {"tracks": [asdict(t) for t in self.tracks], "current_track_id": self.current_track_id},
        )

    def sort(self) -> None:
        self.tracks.sort(key=lambda t: t.created_ts)

    def _ensure_current(self) -> None:
        if self.current_track_id and self.get(self.current_track_id):
            return
        self.current_track_id = self.tracks[0].track_id if self.tracks else None

    def get(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.track_id == track_id:
                return t
        return None

    def current(self) -> Optional[Track]:
        if not self.current_track_id:
            return None
        return self.get(self.current_track_id)

    def set_current(self, track_id: Optional[str]) -> None:
        self.current_track_id = track_id
        self._ensure_current()

    def append_if_new(self, track: Track) -> bool:
        if any(t.track_id == track.track_id for t in self.tracks):
            return False
        # soft dedupe
        for t in self.tracks:
            if t.url == track.url and abs(t.created_ts - track.created_ts) <= 2.0:
                return False

        self.tracks.append(track)
        self.sort()
        self._trim()
        self._ensure_current()
        return True

    def _trim(self) -> None:
        if len(self.tracks) <= QUEUE_LIMIT:
            return

        keep_current = self.current_track_id

        def removable(t: Track) -> bool:
            if keep_current and t.track_id == keep_current:
                return False
            if t.status in {"playing", "paused"}:
                return False
            return True

        self.sort()
        while len(self.tracks) > QUEUE_LIMIT:
            idx = next((i for i, t in enumerate(self.tracks) if removable(t)), None)
            if idx is None:
                break
            victim = self.tracks.pop(idx)
            if victim.local_path:
                try:
                    Path(victim.local_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def index_of_current(self) -> int:
        if not self.current_track_id:
            return -1
        for i, t in enumerate(self.tracks):
            if t.track_id == self.current_track_id:
                return i
        return -1

    def next_id(self) -> Optional[str]:
        i = self.index_of_current()
        if i < 0:
            return None
        if i + 1 >= len(self.tracks):
            return None
        return self.tracks[i + 1].track_id

    def prev_id(self) -> Optional[str]:
        i = self.index_of_current()
        if i <= 0:
            return None
        return self.tracks[i - 1].track_id

    def clear(self) -> None:
        for t in self.tracks:
            if t.local_path:
                try:
                    Path(t.local_path).unlink(missing_ok=True)
                except Exception:
                    pass
        self.tracks.clear()
        self.current_track_id = None
