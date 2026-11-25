"""YouTube RSS feed URLs and parsing (pure functions)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeedEntry:
    video_id: str
    title: str
    published_at: int
    thumbnail_url: str
    url: str
