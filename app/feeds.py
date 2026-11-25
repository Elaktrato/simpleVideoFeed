"""YouTube RSS feed URLs and parsing (pure functions)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FeedEntry:
    video_id: str
    title: str
    published_at: int
    thumbnail_url: str
    url: str


FEED_BASE = "https://www.youtube.com/feeds/videos.xml"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def feed_url(channel_id: str, long_form_only: bool) -> str:
    """Channel uploads feed. The UULF playlist holds long-form uploads only (no Shorts/lives)."""
    if long_form_only and channel_id.startswith("UC"):
        return f"{FEED_BASE}?playlist_id=UULF{channel_id[2:]}"
    return f"{FEED_BASE}?channel_id={channel_id}"


def is_short_url(url: str) -> bool:
    return "/shorts/" in url


def parse_feed(xml: str) -> list[FeedEntry]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out: list[FeedEntry] = []
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS).strip()
        published = entry.findtext("a:published", default="", namespaces=NS).strip()
        if not vid or not published:
            continue
        link = entry.find("a:link[@rel='alternate']", NS)
        thumb = entry.find("media:group/media:thumbnail", NS)
        out.append(
            FeedEntry(
                video_id=vid,
                title=entry.findtext("a:title", default="", namespaces=NS).strip(),
                published_at=int(datetime.fromisoformat(published).timestamp()),
                thumbnail_url=thumb.get("url", "") if thumb is not None else "",
                url=link.get("href", "") if link is not None else f"https://www.youtube.com/watch?v={vid}",
            )
        )
    return out
