from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from donation_media_hub.config import DA_MEDIA_URL, USER_AGENT
from donation_media_hub.models import Track


def _jsonp_to_json(text: str) -> Optional[dict]:
    m = re.search(r"\((\{.*\})\)\s*$", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


class DonationAlertsClient:
    def __init__(self, token: str, last_media_id: int = 0) -> None:
        self.token = token
        self.last_media_id = int(last_media_id or 0)

    def fetch_new_tracks(self) -> Iterable[Track]:
        if not self.token:
            return []

        ts_ms = int(time.time() * 1000)
        params = {"callback": f"jQuery{ts_ms}", "token": self.token, "_": ts_ms}

        r = requests.get(
            DA_MEDIA_URL,
            params=params,
            timeout=15,
            headers={"user-agent": USER_AGENT},
        )
        r.raise_for_status()

        data = _jsonp_to_json(r.text)
        if not data:
            return []

        media_list = data.get("media", []) or []
        new_max = self.last_media_id
        out: list[Track] = []

        for media in media_list:
            try:
                media_id = int(media.get("media_id", 0))
            except Exception:
                continue
            if media_id <= self.last_media_id:
                continue

            if media.get("sub_type") != "youtube":
                continue

            add_raw = media.get("additional_data") or ""
            try:
                add = json.loads(add_raw) if add_raw else {}
            except Exception:
                add = {}

            url = add.get("url")
            if not url:
                continue

            title = media.get("title") or "YouTube"
            created_ts = time.time()
            created_str = media.get("date_created")
            if created_str:
                try:
                    dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                    created_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    created_ts = time.time()

            track_id = f"DA:{media_id}"
            out.append(
                Track(
                    track_id=track_id,
                    source="DA",
                    created_ts=float(created_ts),
                    url=str(url),
                    title=str(title),
                    status="queued",
                )
            )

            if media_id > new_max:
                new_max = media_id

        self.last_media_id = new_max
        return out
