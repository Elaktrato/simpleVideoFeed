"""SQLite connection and schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    youtube_channel_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    handle TEXT,
    url TEXT NOT NULL,
    group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    include_shorts INTEGER NOT NULL DEFAULT 0,
    added_at INTEGER NOT NULL,
    last_checked_at INTEGER,
    last_error TEXT,
    unfollowed_at INTEGER
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    youtube_video_id TEXT NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    published_at INTEGER NOT NULL,
    duration_s INTEGER,
    thumbnail_url TEXT,
    thumbnail_path TEXT,
    file_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    watched_at INTEGER,
    position_s REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS videos_published ON videos(published_at);
CREATE TABLE IF NOT EXISTS queue (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("channels", "unfollowed_at", "INTEGER"),
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, definition in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()
