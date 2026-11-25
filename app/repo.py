"""All SQL lives here."""
from __future__ import annotations

import sqlite3

from app.youtube import ChannelInfo


class AlreadyAdded(Exception):
    pass


# ---------- groups ----------

def create_group(conn: sqlite3.Connection, name: str) -> int:
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM groups").fetchone()[0]
    cur = conn.execute("INSERT INTO groups (name, position) VALUES (?, ?)", (name.strip(), pos))
    conn.commit()
    return cur.lastrowid


def rename_group(conn: sqlite3.Connection, group_id: int, name: str) -> None:
    conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name.strip(), group_id))
    conn.commit()


def delete_group(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()


def list_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM groups ORDER BY name COLLATE NOCASE").fetchall()


# ---------- channels ----------

_CHANNEL_SELECT = """
SELECT c.*, g.name AS group_name FROM channels c LEFT JOIN groups g ON g.id = c.group_id
"""


def add_channel(conn: sqlite3.Connection, info: ChannelInfo, group_id: int | None, now: int) -> int:
    try:
        cur = conn.execute(
            "INSERT INTO channels (youtube_channel_id, title, handle, url, group_id, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (info.channel_id, info.title, info.handle, info.url, group_id, now),
        )
    except sqlite3.IntegrityError as e:
        raise AlreadyAdded(f"{info.title} is already added") from e
    conn.commit()
    return cur.lastrowid


def list_channels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        _CHANNEL_SELECT + " ORDER BY g.name IS NULL, g.name COLLATE NOCASE, c.title COLLATE NOCASE"
    ).fetchall()


def get_channel(conn: sqlite3.Connection, channel_id: int) -> sqlite3.Row | None:
    return conn.execute(_CHANNEL_SELECT + " WHERE c.id = ?", (channel_id,)).fetchone()


def set_channel_group(conn: sqlite3.Connection, channel_id: int, group_id: int | None) -> None:
    conn.execute("UPDATE channels SET group_id = ? WHERE id = ?", (group_id, channel_id))
    conn.commit()


def set_include_shorts(conn: sqlite3.Connection, channel_id: int, include: bool) -> None:
    conn.execute("UPDATE channels SET include_shorts = ? WHERE id = ?", (int(include), channel_id))
    conn.commit()


def record_channel_check(conn: sqlite3.Connection, channel_id: int, now: int, error: str | None) -> None:
    conn.execute(
        "UPDATE channels SET last_checked_at = ?, last_error = ? WHERE id = ?", (now, error, channel_id)
    )
    conn.commit()


def delete_channel(conn: sqlite3.Connection, channel_id: int) -> list[str]:
    """Delete a channel and its videos; returns file paths (videos + thumbs) to unlink."""
    paths: list[str] = []
    for r in conn.execute(
        "SELECT file_path, thumbnail_path FROM videos WHERE channel_id = ?", (channel_id,)
    ):
        paths += [p for p in (r["file_path"], r["thumbnail_path"]) if p]
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()
    return paths


# ---------- videos ----------

VISIBLE_STATUSES = ("pending", "downloading", "ready", "failed")
WINDOWS = {"today": 86400, "week": 7 * 86400}
WATCHED_RATIO = 0.9
MAX_ATTEMPTS = 3


def upsert_video(conn: sqlite3.Connection, channel_id: int, entry, status: str, now: int) -> bool:
    """Insert a video if new. Existing rows are left untouched. Returns True if inserted."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO videos"
        " (youtube_video_id, channel_id, title, published_at, thumbnail_url, status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (entry.video_id, channel_id, entry.title, entry.published_at, entry.thumbnail_url, status),
    )
    conn.commit()
    return cur.rowcount == 1


def get_video(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT v.*, c.title AS channel_title FROM videos v JOIN channels c ON c.id = v.channel_id"
        " WHERE v.id = ?",
        (video_id,),
    ).fetchone()


def get_video_by_yid(conn: sqlite3.Connection, youtube_video_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM videos WHERE youtube_video_id = ?", (youtube_video_id,)
    ).fetchone()


def videos_with_status(conn: sqlite3.Connection, channel_id: int, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM videos WHERE channel_id = ? AND status = ?", (channel_id, status)
    ).fetchall()


def set_video_meta(conn: sqlite3.Connection, video_id: int, duration_s: int | None, status: str) -> None:
    conn.execute(
        "UPDATE videos SET duration_s = COALESCE(?, duration_s), status = ? WHERE id = ?",
        (duration_s, status, video_id),
    )
    conn.commit()


def set_status(
    conn: sqlite3.Connection,
    video_id: int,
    status: str,
    error: str | None = None,
    file_path: str | None = None,
) -> None:
    conn.execute(
        "UPDATE videos SET status = ?, error = ?, file_path = COALESCE(?, file_path) WHERE id = ?",
        (status, error, file_path, video_id),
    )
    conn.commit()


def clear_file(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("UPDATE videos SET file_path = NULL WHERE id = ?", (video_id,))
    conn.commit()


def set_thumbnail(conn: sqlite3.Connection, video_id: int, path: str) -> None:
    conn.execute("UPDATE videos SET thumbnail_path = ? WHERE id = ?", (path, video_id))
    conn.commit()


def claim_next_pending(conn: sqlite3.Connection, now: int) -> sqlite3.Row | None:
    """Atomically pick the oldest downloadable video and mark it 'downloading'.

    Safe with several download workers on separate connections.
    """
    row = conn.execute(
        "UPDATE videos SET status = 'downloading' WHERE id = ("
        "  SELECT id FROM videos WHERE status = 'pending' AND attempts < ? AND next_attempt_at <= ?"
        "  ORDER BY published_at ASC LIMIT 1"
        ") AND status = 'pending' RETURNING *",
        (MAX_ATTEMPTS, now),
    ).fetchone()
    conn.commit()
    return row


def set_next_attempt(conn: sqlite3.Connection, video_id: int, ts: int) -> None:
    conn.execute("UPDATE videos SET next_attempt_at = ? WHERE id = ?", (ts, video_id))
    conn.commit()


def bump_attempts(conn: sqlite3.Connection, video_id: int) -> int:
    conn.execute("UPDATE videos SET attempts = attempts + 1 WHERE id = ?", (video_id,))
    conn.commit()
    return conn.execute("SELECT attempts FROM videos WHERE id = ?", (video_id,)).fetchone()[0]


def reset_attempts(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("UPDATE videos SET attempts = 0, next_attempt_at = 0 WHERE id = ?", (video_id,))
    conn.commit()


def _feed_where(window: str, group_id: int | None, now: int) -> tuple[str, list]:
    placeholders = ",".join("?" * len(VISIBLE_STATUSES))
    sql = (
        f" WHERE v.status IN ({placeholders}) AND v.watched_at IS NULL AND v.published_at >= ?"
    )
    args: list = [*VISIBLE_STATUSES, now - WINDOWS[window]]
    if group_id is not None:
        sql += " AND c.group_id = ?"
        args.append(group_id)
    return sql, args


def feed(conn: sqlite3.Connection, window: str, group_id: int | None, now: int) -> list[sqlite3.Row]:
    where, args = _feed_where(window, group_id, now)
    return conn.execute(
        "SELECT v.*, c.title AS channel_title,"
        " EXISTS(SELECT 1 FROM queue q WHERE q.video_id = v.id) AS in_queue"
        " FROM videos v JOIN channels c ON c.id = v.channel_id"
        + where
        + " ORDER BY v.published_at DESC",
        args,
    ).fetchall()


def feed_counts(conn: sqlite3.Connection, group_id: int | None, now: int) -> dict[str, int]:
    out = {}
    for window in WINDOWS:
        where, args = _feed_where(window, group_id, now)
        out[window] = conn.execute(
            "SELECT COUNT(*) FROM videos v JOIN channels c ON c.id = v.channel_id" + where, args
        ).fetchone()[0]
    return out


def mark_watched(conn: sqlite3.Connection, video_id: int, now: int) -> None:
    conn.execute("UPDATE videos SET watched_at = ? WHERE id = ?", (now, video_id))
    conn.execute("DELETE FROM queue WHERE video_id = ?", (video_id,))
    conn.commit()


def unmark_watched(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("UPDATE videos SET watched_at = NULL WHERE id = ?", (video_id,))
    conn.commit()


def save_progress(
    conn: sqlite3.Connection, video_id: int, position_s: float, duration_s: float, now: int
) -> bool:
    """Store playback position. Returns True if this call marked the video watched."""
    conn.execute("UPDATE videos SET position_s = ? WHERE id = ?", (max(0.0, position_s), video_id))
    conn.commit()
    if duration_s and duration_s > 0 and position_s / duration_s >= WATCHED_RATIO:
        row = conn.execute("SELECT watched_at FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row and row["watched_at"] is None:
            mark_watched(conn, video_id, now)
            return True
    return False


# ---------- queue ----------

def queue_add(conn: sqlite3.Connection, video_ids: list[int]) -> None:
    for vid in video_ids:
        row = conn.execute(
            "SELECT watched_at FROM videos WHERE id = ? AND status NOT IN ('expired','skipped')",
            (vid,),
        ).fetchone()
        if row is None or row["watched_at"] is not None:
            continue
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM queue").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO queue (video_id, position) VALUES (?, ?)", (vid, pos))
    conn.commit()


def queue_remove(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("DELETE FROM queue WHERE video_id = ?", (video_id,))
    conn.commit()


def queue_move(conn: sqlite3.Connection, video_id: int, direction: str) -> None:
    me = conn.execute("SELECT position FROM queue WHERE video_id = ?", (video_id,)).fetchone()
    if me is None:
        return
    if direction == "up":
        other = conn.execute(
            "SELECT video_id, position FROM queue WHERE position < ? ORDER BY position DESC LIMIT 1",
            (me["position"],),
        ).fetchone()
    else:
        other = conn.execute(
            "SELECT video_id, position FROM queue WHERE position > ? ORDER BY position ASC LIMIT 1",
            (me["position"],),
        ).fetchone()
    if other is None:
        return
    conn.execute("UPDATE queue SET position = ? WHERE video_id = ?", (other["position"], video_id))
    conn.execute("UPDATE queue SET position = ? WHERE video_id = ?", (me["position"], other["video_id"]))
    conn.commit()


def queue_list(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT v.*, c.title AS channel_title FROM queue q"
        " JOIN videos v ON v.id = q.video_id JOIN channels c ON c.id = v.channel_id"
        " ORDER BY q.position"
    ).fetchall()
