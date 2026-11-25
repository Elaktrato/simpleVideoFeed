"""Background work: polling feeds, downloading, cleanup. All functions are synchronous."""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from app import repo
from app.config import Settings
from app.feeds import feed_url, is_short_url, parse_feed

log = logging.getLogger(__name__)

WEEK = 7 * 86400
_INTERMEDIATE = re.compile(r"\.f\d+\.|\.part$|\.ytdl$|\.temp\.")


def _status_from_meta(meta, entry_url: str, video_id: str, check_shorts: bool, yt) -> str:
    if meta is not None and meta.live_status in ("is_live", "is_upcoming"):
        return "waiting"
    if meta is not None and meta.live_status in ("was_live", "post_live"):
        return "skipped"
    if check_shorts and (is_short_url(entry_url) or yt.is_short(video_id)):
        return "skipped"
    return "pending"


def _meta_or_none(yt, settings: Settings, video_id: str):
    try:
        return yt.fetch_meta(video_id, settings.cookies_file)
    except yt.YouTubeError as e:
        log.info("metadata failed for %s: %s", video_id, e)
        return None


def poll_channel(conn: sqlite3.Connection, settings: Settings, channel: sqlite3.Row, yt, now: int) -> int:
    """Fetch a channel's feed and record new videos. Never raises YouTubeError."""
    cid = channel["youtube_channel_id"]
    long_form_only = not channel["include_shorts"]
    fallback_shorts = False
    try:
        try:
            xml = yt.fetch_feed(feed_url(cid, long_form_only))
        except yt.YouTubeError as e:
            if not (long_form_only and "404" in str(e)):
                raise
            xml = yt.fetch_feed(feed_url(cid, False))
            fallback_shorts = True
    except yt.YouTubeError as e:
        repo.record_channel_check(conn, channel["id"], now, str(e))
        return 0

    new = 0
    for entry in parse_feed(xml):
        if entry.published_at < now - WEEK:
            continue
        if not repo.upsert_video(conn, channel["id"], entry, "waiting", now):
            continue
        new += 1
        video = repo.get_video_by_yid(conn, entry.video_id)
        meta = _meta_or_none(yt, settings, entry.video_id)
        status = _status_from_meta(meta, entry.url, entry.video_id, fallback_shorts, yt)
        repo.set_video_meta(conn, video["id"], meta.duration_s if meta else None, status)
        if status != "skipped" and entry.thumbnail_url:
            dest = settings.thumbs_dir / f"{entry.video_id}.jpg"
            try:
                settings.thumbs_dir.mkdir(parents=True, exist_ok=True)
                yt.download_thumbnail(entry.thumbnail_url, dest)
                repo.set_thumbnail(conn, video["id"], str(dest))
            except Exception as e:  # thumbnails are cosmetic
                log.info("thumbnail failed for %s: %s", entry.video_id, e)

    # Live / upcoming videos are re-checked on every poll.
    for video in repo.videos_with_status(conn, channel["id"], "waiting"):
        if video["published_at"] < now - WEEK:
            repo.set_video_meta(conn, video["id"], None, "skipped")
            continue
        meta = _meta_or_none(yt, settings, video["youtube_video_id"])
        if meta is None:
            continue
        status = _status_from_meta(meta, "", video["youtube_video_id"], False, yt)
        repo.set_video_meta(conn, video["id"], meta.duration_s, status)

    repo.record_channel_check(conn, channel["id"], now, None)
    return new


def poll_all(conn: sqlite3.Connection, settings: Settings, yt, now: int) -> None:
    for channel in repo.list_channels(conn):
        try:
            poll_channel(conn, settings, channel, yt, now)
        except Exception as e:
            log.exception("polling %s failed", channel["title"])
            repo.record_channel_check(conn, channel["id"], now, f"Unexpected error: {e}")


RETRY_BASE_SECONDS = 600  # 10 min, doubling per attempt


def download_one(conn: sqlite3.Connection, settings: Settings, yt, now: int) -> bool:
    """Download the oldest eligible pending video. Returns False if there was nothing to do."""
    video = repo.claim_next_pending(conn, now)
    if video is None:
        return False
    try:
        path = yt.download_video(
            video["youtube_video_id"], settings.videos_dir, settings.max_height, settings.cookies_file
        )
    except Exception as e:
        attempts = repo.bump_attempts(conn, video["id"])
        status = "failed" if attempts >= repo.MAX_ATTEMPTS else "pending"
        repo.set_next_attempt(conn, video["id"], now + RETRY_BASE_SECONDS * 2 ** (attempts - 1))
        repo.set_status(conn, video["id"], status, error=str(e))
        log.info("download of %s failed (%d): %s", video["youtube_video_id"], attempts, e)
        return True
    repo.set_status(conn, video["id"], "ready", file_path=str(path))
    return True


def _unlink(path: str | None) -> bool:
    if not path:
        return False
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False


def cleanup(conn: sqlite3.Connection, settings: Settings, now: int) -> int:
    deleted = 0
    grace_cutoff = now - settings.watched_grace_hours * 3600
    for v in conn.execute(
        "SELECT id, file_path FROM videos WHERE watched_at IS NOT NULL AND watched_at <= ?"
        " AND file_path IS NOT NULL",
        (grace_cutoff,),
    ).fetchall():
        deleted += _unlink(v["file_path"])
        repo.clear_file(conn, v["id"])

    retention_cutoff = now - settings.retention_days * 86400
    for v in conn.execute(
        "SELECT id, file_path, thumbnail_path FROM videos WHERE published_at <= ? AND status != 'expired'",
        (retention_cutoff,),
    ).fetchall():
        deleted += _unlink(v["file_path"])
        _unlink(v["thumbnail_path"])
        conn.execute(
            "UPDATE videos SET status = 'expired', file_path = NULL, thumbnail_path = NULL WHERE id = ?",
            (v["id"],),
        )
        conn.execute("DELETE FROM queue WHERE video_id = ?", (v["id"],))
    conn.commit()
    return deleted


def recover(conn: sqlite3.Connection, settings: Settings) -> None:
    """Run at startup: undo the effects of a crash or restart mid-download."""
    conn.execute("UPDATE videos SET status = 'pending' WHERE status = 'downloading'")
    conn.commit()
    if settings.videos_dir.exists():
        for p in settings.videos_dir.iterdir():
            if p.is_file() and _INTERMEDIATE.search(p.name):
                p.unlink(missing_ok=True)
