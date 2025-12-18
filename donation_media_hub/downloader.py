from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import requests

from donation_media_hub.config import USER_AGENT, YT_DL_API
from donation_media_hub.models import Track
from donation_media_hub.services.youtube import is_youtube_url, sanitize_filename


class Downloader:
    """
    Single responsibility: download mp3 for a track using YT_DL_API.
    """

    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def download_mp3(self, track: Track, timeout: int = 40) -> Path:
        if not is_youtube_url(track.url):
            raise ValueError("Not a YouTube URL")

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        safe = sanitize_filename(track.title)
        out = self.temp_dir / f"{safe}.mp3"
        if out.exists():
            out = self.temp_dir / f"{safe}__{int(track.created_ts)}.mp3"

        r = requests.get(
            YT_DL_API,
            params={"url": track.url},
            timeout=timeout,
            headers={"user-agent": USER_AGENT},
        )
        r.raise_for_status()

        out.write_bytes(r.content)
        return out

    @staticmethod
    def cleanup_keep(temp_dir: Path, keep_paths: set[str]) -> None:
        if not temp_dir.exists():
            return
        for p in temp_dir.glob("*.mp3"):
            rp = str(p.resolve())
            if rp not in keep_paths:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
