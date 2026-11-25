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
