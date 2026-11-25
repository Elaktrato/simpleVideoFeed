from pathlib import Path

from app.config import load_settings
from app.db import connect, init_db


def test_load_settings_defaults():
    s = load_settings({})
    assert s.poll_minutes == 30
    assert s.max_height == 720
    assert s.retention_days == 14
    assert s.watched_grace_hours == 24
    assert s.max_concurrent_downloads == 1
    assert s.cookies_file is None
    assert s.data_dir == Path("/data")
    assert s.db_path == Path("/data/feed.db")
    assert s.videos_dir == Path("/data/videos")
    assert s.thumbs_dir == Path("/data/thumbs")


def test_load_settings_overrides():
    s = load_settings({"MAX_HEIGHT": "480", "DATA_DIR": "/x", "YTDLP_COOKIES_FILE": "/c.txt"})
    assert s.max_height == 480
    assert s.data_dir == Path("/x")
    assert s.cookies_file == "/c.txt"


def test_init_db_creates_tables_idempotent():
    c = connect(":memory:")
    init_db(c)
    init_db(c)
    names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"groups", "channels", "videos", "queue"} <= names
