from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Track:
    track_id: str
    source: str
    created_ts: float
    url: str
    title: str
    status: str = (
        "queued"  # queued/downloading/ready/playing/paused/played/skipped/failed
    )
    local_path: Optional[str] = None
    error: Optional[str] = None
