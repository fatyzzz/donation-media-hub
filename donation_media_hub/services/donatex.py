from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from donation_media_hub.config import DX_API_URL, USER_AGENT
from donation_media_hub.models import Track
from donation_media_hub.services.youtube import is_youtube_url, youtube_oembed_title


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_ts(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class DonateXClient:
    def __init__(self, token: str, last_timestamp: Optional[float] = None) -> None:
        self.token = token
        self.last_timestamp = float(last_timestamp) if last_timestamp else None
        self._title_cache: dict[str, str] = {}

    def fetch_new_tracks(self) -> Iterable[Track]:
        if not self.token:
            return []

        headers = {
            "accept": "application/json",
            "x-external-token": self.token,
            "user-agent": USER_AGENT,
        }
        params = {"skip": 0, "take": 20, "hideTest": "true", "withAi": "true"}

        r = requests.get(DX_API_URL, headers=headers, params=params, timeout=15)
        r.raise_for_status()

        data = r.json()
        donations = data.get("donations", []) or []
        if not donations:
            return []

        donations.sort(key=lambda d: parse_iso_ts(d.get("timestamp", now_iso())))
        out: list[Track] = []

        updated_last = self.last_timestamp
        for d in donations:
            ts_str = d.get("timestamp")
            if not ts_str:
                continue
            ts = parse_iso_ts(ts_str)
            if self.last_timestamp is not None and ts <= self.last_timestamp:
                continue

            url = d.get("musicLink")
            if not url:
                continue

            if url in self._title_cache:
                title = self._title_cache[url]
            else:
                title = youtube_oembed_title(url) if is_youtube_url(url) else None
                title = title or "Track"
                self._title_cache[url] = title

            stable_suffix = d.get("id") or ts_str
            track_id = f"DX:{stable_suffix}"

            out.append(
                Track(
                    track_id=track_id,
                    source="DX",
                    created_ts=float(ts),
                    url=str(url),
                    title=str(title),
                    status="queued",
                )
            )

            updated_last = ts

        if updated_last is not None:
            self.last_timestamp = float(updated_last)
        return out
