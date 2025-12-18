from __future__ import annotations

import re
from typing import Optional

import requests

from donation_media_hub.config import USER_AGENT


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "track"


def youtube_oembed_title(url: str, timeout: int = 10) -> Optional[str]:
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=timeout,
            headers={"user-agent": USER_AGENT},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("title")
    except Exception:
        return None
