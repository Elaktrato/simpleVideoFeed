"""A stand-in for app.youtube used by job and API tests."""
from datetime import datetime, timezone
from pathlib import Path

from app.youtube import ChannelInfo, VideoMeta, YouTubeError


def feed_xml(entries):
    """entries: list of (video_id, published_unix, url_or_None)."""
    parts = []
    for vid, ts, url in entries:
        iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
        href = url or f"https://www.youtube.com/watch?v={vid}"
        parts.append(
            f"<entry><yt:videoId>{vid}</yt:videoId><title>T {vid}</title>"
            f'<link rel="alternate" href="{href}"/><published>{iso}</published>'
            f'<media:group><media:thumbnail url="https://i.ytimg.com/vi/{vid}/hq.jpg"/></media:group></entry>'
        )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"'
        ' xmlns:media="http://search.yahoo.com/mrss/">' + "".join(parts) + "</feed>"
    )


class FakeYT:
    YouTubeError = YouTubeError

    def __init__(self):
        self.feeds = {}          # url -> xml str or Exception
        self.meta = {}           # video_id -> VideoMeta or Exception
        self.shorts = set()
        self.channels = {}       # url -> ChannelInfo or Exception
        self.download_errors = {}  # video_id -> list of errors to raise in order
        self.requested = []
        self.downloads = []

    def resolve_channel(self, url, cookies_file):
        r = self.channels.get(url)
        if r is None:
            raise YouTubeError("Couldn't find a channel at that link")
        if isinstance(r, Exception):
            raise r
        return r

    def fetch_feed(self, url):
        self.requested.append(url)
        r = self.feeds.get(url)
        if r is None:
            raise YouTubeError("Feed request failed with HTTP 404")
        if isinstance(r, Exception):
            raise r
        return r

    def fetch_meta(self, video_id, cookies_file):
        r = self.meta.get(video_id, VideoMeta(duration_s=600, live_status="not_live"))
        if isinstance(r, Exception):
            raise r
        return r

    def is_short(self, video_id):
        return video_id in self.shorts

    def download_thumbnail(self, url, dest: Path):
        dest.write_bytes(b"JPG")

    def download_video(self, video_id, videos_dir: Path, max_height, cookies_file):
        errs = self.download_errors.get(video_id)
        if errs:
            raise errs.pop(0)
        self.downloads.append(video_id)
        p = videos_dir / f"{video_id}.mp4"
        p.write_bytes(b"0" * 1000)
        return p


def channel_info(cid="UCaaa", title="Chan A"):
    return ChannelInfo(cid, title, None, f"https://www.youtube.com/channel/{cid}")
