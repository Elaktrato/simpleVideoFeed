import pytest
from fastapi.testclient import TestClient

from app import repo
from app.db import connect
from app.main import create_app
from tests.fakeyt import FakeYT
from tests.helpers import H, make_channel, make_video


@pytest.fixture
def env(settings, now):
    app = create_app(settings, yt=FakeYT(), start_background=False, clock=lambda: now)
    with TestClient(app) as client:
        db = connect(settings.db_path)
        yield client, db
        db.close()


def ready(db, settings, c, yid, published_at, title=None):
    v = make_video(db, c, yid, published_at, status="pending", title=title)
    f = settings.videos_dir / f"{yid}.mp4"
    f.write_bytes(b"x")
    repo.set_status(db, v, "ready", file_path=str(f))
    return v


def test_feed_empty_shows_caught_up(env):
    client, _ = env
    r = client.get("/")
    assert r.status_code == 200
    assert "You're all caught up." in r.text


def test_feed_tabs_show_counts(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    ready(db, settings, c, "a", now - H)
    ready(db, settings, c, "b", now - 30 * H)
    html = client.get("/").text
    assert "Today (1)" in html and "This Week (2)" in html
    week = client.get("/?tab=week").text
    assert "Video a" in week and "Video b" in week
    assert "Video b" not in client.get("/?tab=today").text


def test_feed_item_shows_title_channel_duration_age(env, settings, now):
    client, db = env
    c = make_channel(db, now, title="Bake Lab")
    v = ready(db, settings, c, "a", now - 3 * H, title="Sourdough")
    db.execute("UPDATE videos SET duration_s = 842 WHERE id = ?", (v,))
    db.commit()
    html = client.get("/").text
    assert "Sourdough" in html and "Bake Lab" in html and "14:02" in html and "3h ago" in html


def test_feed_never_references_youtube_hosts(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = ready(db, settings, c, "a", now - H)
    repo.set_thumbnail(db, v, str(settings.thumbs_dir / "a.jpg"))
    html = client.get("/").text
    assert "ytimg.com" not in html
    assert "youtube.com/embed" not in html
    assert "/thumbs/a.jpg" in html


def test_feed_group_chip_filters(env, settings, now):
    client, db = env
    g = repo.create_group(db, "Tech")
    a = make_channel(db, now, "UCa", "A", group_id=g)
    b = make_channel(db, now, "UCb", "B")
    ready(db, settings, a, "va", now - H)
    ready(db, settings, b, "vb", now - H)
    html = client.get(f"/?group={g}").text
    assert "Video va" in html and "Video vb" not in html
    assert ">Tech<" in html


def test_not_ready_item_has_no_play_link(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    p = make_video(db, c, "p", now - H, status="pending")
    f = make_video(db, c, "f", now - H, status="failed")
    html = client.get("/").text
    assert f'href="/watch/{p}"' not in html
    assert "downloading…" in html
    assert "failed — retry" in html


def test_watch_page_has_video_and_resume_position(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = ready(db, settings, c, "w", now - H)
    repo.save_progress(db, v, 42, 600, now)
    html = client.get(f"/watch/{v}").text
    assert 'src="/media/w.mp4"' in html and 'data-position="42' in html


def test_watch_expired_shows_unavailable(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = make_video(db, c, "e", now - H, status="expired")
    assert "This video is no longer available." in client.get(f"/watch/{v}").text
    assert "This video is no longer available." in client.get("/watch/9999").text


def test_channels_page_shows_error_param(env, now):
    client, db = env
    make_channel(db, now, title="Bake Lab")
    html = client.get("/channels?error=Couldn%27t%20find%20a%20channel").text
    assert "Couldn&#39;t find a channel" in html or "Couldn't find a channel" in html
    assert "Bake Lab" in html


def test_queue_page_renders_items(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = ready(db, settings, c, "q", now - H, title="Queued one")
    repo.queue_add(db, [v])
    html = client.get("/queue").text
    assert "Queued one" in html
    assert 'Queue (<span id="queue-count">1</span>)' in html


def test_queue_page_empty(env):
    client, _ = env
    assert "Queue finished." in client.get("/queue").text


def test_static_assets_served(env):
    client, _ = env
    css = client.get("/static/style.css")
    assert css.status_code == 200 and "#1a1b26" in css.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/manifest.webmanifest").status_code == 200


def test_css_respects_hidden_attribute(env):
    """Elements with display rules (.toast, .player, .bulk) must still hide via [hidden]."""
    client, _ = env
    css = client.get("/static/style.css").text.replace(" ", "")
    assert "[hidden]{display:none!important;}" in css
