import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import jobs, repo
from app.db import connect, init_db
from app.feeds import feed_url
from app.main import create_app
from tests.fakeyt import FakeYT, channel_info, feed_xml
from tests.helpers import H, make_channel, make_video


def ready(conn, settings, channel_id, yid, now):
    v = make_video(conn, channel_id, yid, now - H, status="pending")
    f = settings.videos_dir / f"{yid}.mp4"
    f.write_bytes(b"x")
    repo.set_status(conn, v, "ready", file_path=str(f))
    return v


def qids(conn):
    return [r["id"] for r in repo.queue_list(conn)]


@pytest.fixture
def two(conn, settings, now):
    a = make_channel(conn, now, "UCaaa", "Chan A")
    b = make_channel(conn, now, "UCbbb", "Chan B")
    return a, b


def test_init_db_migrates_old_channels_table():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE channels (id INTEGER PRIMARY KEY, youtube_channel_id TEXT NOT NULL UNIQUE,"
        " title TEXT NOT NULL, handle TEXT, url TEXT NOT NULL, group_id INTEGER,"
        " include_shorts INTEGER NOT NULL DEFAULT 0, added_at INTEGER NOT NULL,"
        " last_checked_at INTEGER, last_error TEXT)"
    )
    c.execute("INSERT INTO channels (youtube_channel_id, title, url, added_at) VALUES ('UCx','X','u',1)")
    c.commit()
    init_db(c)
    init_db(c)
    cols = [r["name"] for r in c.execute("PRAGMA table_info(channels)")]
    assert "unfollowed_at" in cols
    assert c.execute("SELECT title FROM channels").fetchone()["title"] == "X"


def test_unfollow_dequeues_all_but_kept(conn, settings, now, two):
    a, _ = two
    a1, a2, a3 = (ready(conn, settings, a, y, now) for y in ("a1", "a2", "a3"))
    repo.queue_add(conn, [a1, a2, a3])
    assert repo.unfollow_channel(conn, a, a1, now) is True
    assert qids(conn) == [a1]


def test_unfollow_keeps_other_channels_order(conn, settings, now, two):
    a, b = two
    b1, a1, b2, a2 = (ready(conn, settings, c, y, now) for c, y in ((b, "b1"), (a, "a1"), (b, "b2"), (a, "a2")))
    repo.queue_add(conn, [b1, a1, b2, a2])
    repo.unfollow_channel(conn, a, a1, now)
    assert qids(conn) == [b1, a1, b2]


def test_unfollow_ignores_keep_from_other_channel(conn, settings, now, two):
    a, b = two
    a1 = ready(conn, settings, a, "a1", now)
    b1 = ready(conn, settings, b, "b1", now)
    repo.queue_add(conn, [a1, b1])
    repo.unfollow_channel(conn, a, b1, now)
    assert qids(conn) == [b1]


def test_unfollow_unknown_channel_returns_false(conn, now):
    assert repo.unfollow_channel(conn, 999, None, now) is False


def test_unfollowed_hidden_from_feed_counts_and_list(conn, settings, now, two):
    a, b = two
    ready(conn, settings, a, "a1", now)
    ready(conn, settings, b, "b1", now)
    repo.unfollow_channel(conn, a, None, now)
    assert [r["youtube_video_id"] for r in repo.feed(conn, "week", None, now)] == ["b1"]
    assert repo.feed_counts(conn, None, now) == {"today": 1, "week": 1}
    assert [c["id"] for c in repo.list_channels(conn)] == [b]


def test_unfollowed_videos_not_queueable(conn, settings, now, two):
    a, b = two
    a2 = ready(conn, settings, a, "a2", now)
    b1 = ready(conn, settings, b, "b1", now)
    repo.queue_add(conn, [b1])
    repo.unfollow_channel(conn, a, None, now)
    repo.queue_add(conn, [a2])
    repo.queue_play_next(conn, a2)
    repo.queue_play_now(conn, a2, True)
    assert qids(conn) == [b1]


def test_poll_all_skips_unfollowed(conn, settings, now, two):
    a, b = two
    yt = FakeYT()
    yt.feeds[feed_url("UCbbb", True)] = feed_xml([])
    repo.unfollow_channel(conn, a, None, now)
    jobs.poll_all(conn, settings, yt, now)
    assert not any("UCaaa" in u or "UULFaaa" in u for u in yt.requested)
    assert any("bbb" in u for u in yt.requested)


def test_cleanup_deletes_unfollowed_only_when_nothing_queued(conn, settings, now, two):
    a, _ = two
    a1 = ready(conn, settings, a, "a1", now)
    f = settings.videos_dir / "a1.mp4"
    repo.queue_add(conn, [a1])
    repo.unfollow_channel(conn, a, a1, now)
    jobs.cleanup(conn, settings, now)
    assert repo.get_channel(conn, a) is not None and f.exists()
    repo.queue_remove(conn, a1)
    jobs.cleanup(conn, settings, now)
    assert repo.get_channel(conn, a) is None
    assert repo.get_video(conn, a1) is None
    assert not f.exists()


def test_refollow_clears_unfollowed_and_survives_cleanup(conn, settings, now, two):
    a, _ = two
    ready(conn, settings, a, "a1", now)
    g = repo.create_group(conn, "Tech")
    repo.unfollow_channel(conn, a, None, now)
    assert repo.refollow_channel(conn, "UCaaa", g) == a
    ch = repo.get_channel(conn, a)
    assert ch["unfollowed_at"] is None and ch["group_id"] == g
    jobs.cleanup(conn, settings, now)
    assert repo.get_channel(conn, a) is not None
    assert [r["youtube_video_id"] for r in repo.feed(conn, "week", None, now)] == ["a1"]


def test_refollow_returns_none_when_not_unfollowed(conn, now, two):
    assert repo.refollow_channel(conn, "UCaaa", None) is None
    assert repo.refollow_channel(conn, "UCzzz", None) is None


@pytest.fixture
def env(settings, now):
    yt = FakeYT()
    app = create_app(settings, yt=yt, start_background=False, clock=lambda: now)
    with TestClient(app) as client:
        db = connect(settings.db_path)
        yield client, db, yt
        db.close()


def test_api_unfollow_returns_queue_and_404(env, settings, now):
    client, db, _ = env
    a = make_channel(db, now, "UCaaa", "Chan A")
    a1 = ready(db, settings, a, "a1", now)
    a2 = ready(db, settings, a, "a2", now)
    repo.queue_add(db, [a1, a2])
    r = client.post(f"/api/channels/{a}/unfollow", json={"keep_video_id": a1})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body] == [a1]
    assert body[0]["channel_id"] == a
    assert client.post("/api/channels/999/unfollow", json={}).status_code == 404


def test_api_unfollow_without_body_drops_everything(env, settings, now):
    client, db, _ = env
    a = make_channel(db, now, "UCaaa", "Chan A")
    a1 = ready(db, settings, a, "a1", now)
    repo.queue_add(db, [a1])
    assert client.post(f"/api/channels/{a}/unfollow").json() == []


def test_add_form_refollows_unfollowed_channel(env, now):
    client, db, yt = env
    a = make_channel(db, now, "UCaaa", "Chan A")
    repo.unfollow_channel(db, a, None, now)
    yt.channels["https://www.youtube.com/@a"] = channel_info("UCaaa", "Chan A")
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([])
    r = client.post("/channels", data={"url": "https://www.youtube.com/@a", "group_id": ""}, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    assert [c["id"] for c in repo.list_channels(db)] == [a]
