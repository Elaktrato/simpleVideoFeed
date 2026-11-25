"""The only module that talks to YouTube (yt-dlp + plain HTTP)."""
from __future__ import annotations

from dataclasses import dataclass


class YouTubeError(Exception):
    """Anything that went wrong talking to YouTube, with a readable message."""


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    handle: str | None
    url: str


@dataclass
class VideoMeta:
    duration_s: int | None
    live_status: str | None
