"""The only module that talks to YouTube (yt-dlp + plain HTTP)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp


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


log = logging.getLogger(__name__)

# Tests replace this with httpx.MockTransport.
_transport: httpx.BaseTransport | None = None
_UA = "Mozilla/5.0 (X11; Linux x86_64) simpleVideoFeed"


def _client(follow_redirects: bool = True) -> httpx.Client:
    return httpx.Client(
        timeout=15, follow_redirects=follow_redirects, transport=_transport, headers={"User-Agent": _UA}
    )


def _base_opts(cookies_file: str | None) -> dict:
    o = {"quiet": True, "no_warnings": True, "noprogress": True}
    if cookies_file:
        o["cookiefile"] = cookies_file
    return o


def _normalize_channel_url(url: str) -> str:
    url = url.strip()
    if url.startswith("@"):
        return f"https://www.youtube.com/{url}"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
        raise YouTubeError("That doesn't look like a YouTube link")
    return url


def resolve_channel(url: str, cookies_file: str | None) -> ChannelInfo:
    url = _normalize_channel_url(url)
    opts = {**_base_opts(cookies_file), "extract_flat": True, "playlistend": 1}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False) or {}
    except Exception as e:  # yt-dlp raises many types
        log.info("resolve_channel failed for %s: %s", url, e)
        raise YouTubeError("Couldn't find a channel at that link") from e
    cid = info.get("channel_id")
    if not (isinstance(cid, str) and cid.startswith("UC")):
        cid = next(
            (v for v in (info.get("uploader_id"), info.get("id")) if isinstance(v, str) and v.startswith("UC")),
            None,
        )
    if not cid:
        raise YouTubeError("Couldn't find a channel at that link")
    title = info.get("channel") or info.get("uploader") or info.get("title") or cid
    for suffix in (" - Videos", " - Home", " - Shorts"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    handle = info.get("uploader_id") if str(info.get("uploader_id", "")).startswith("@") else None
    return ChannelInfo(
        channel_id=cid,
        title=title,
        handle=handle,
        url=info.get("channel_url") or f"https://www.youtube.com/channel/{cid}",
    )


def fetch_feed(url: str) -> str:
    try:
        with _client() as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        raise YouTubeError(f"Couldn't reach YouTube: {e}") from e
    if r.status_code != 200:
        raise YouTubeError(f"Feed request failed with HTTP {r.status_code}")
    return r.text


def fetch_meta(video_id: str, cookies_file: str | None) -> VideoMeta:
    try:
        with yt_dlp.YoutubeDL(_base_opts(cookies_file)) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False, process=False
            ) or {}
    except Exception as e:
        raise YouTubeError(str(e)) from e
    dur = info.get("duration")
    return VideoMeta(duration_s=int(dur) if dur else None, live_status=info.get("live_status"))


def download_thumbnail(url: str, dest: Path) -> None:
    try:
        with _client() as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        raise YouTubeError(str(e)) from e
    if r.status_code != 200:
        raise YouTubeError(f"Thumbnail HTTP {r.status_code}")
    dest.write_bytes(r.content)


def is_short(video_id: str) -> bool:
    """A /shorts/ URL answers 200 for Shorts and redirects for normal videos."""
    try:
        with _client(follow_redirects=False) as c:
            return c.head(f"https://www.youtube.com/shorts/{video_id}").status_code == 200
    except httpx.HTTPError:
        return False


def ydl_download_opts(video_id: str, videos_dir: Path, max_height: int, cookies_file: str | None) -> dict:
    h = max_height
    return {
        **_base_opts(cookies_file),
        "format": (
            f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/b[height<={h}][ext=mp4]"
            f"/bv*[height<={h}]+ba/b[height<={h}]"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(videos_dir / "%(id)s.%(ext)s"),
    }


def _remove_partials(video_id: str, videos_dir: Path) -> None:
    """Delete every file yt-dlp may have left for this video (.part, .fNNN, .ytdl, …)."""
    for p in videos_dir.glob(f"{video_id}.*"):
        p.unlink(missing_ok=True)


def download_video(video_id: str, videos_dir: Path, max_height: int, cookies_file: str | None) -> Path:
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / f"{video_id}.mp4"
    try:
        with yt_dlp.YoutubeDL(ydl_download_opts(video_id, videos_dir, max_height, cookies_file)) as ydl:
            ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except Exception as e:
        _remove_partials(video_id, videos_dir)
        raise YouTubeError(str(e).replace("ERROR: ", "")) from e
    if not dest.exists():
        _remove_partials(video_id, videos_dir)
        raise YouTubeError("Download finished but no MP4 file was produced")
    return dest
