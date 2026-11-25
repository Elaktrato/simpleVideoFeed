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


def test_channels_page_shows_error_param(env, now):
    client, db = env
    make_channel(db, now, title="Bake Lab")
    html = client.get("/channels?error=Couldn%27t%20find%20a%20channel").text
    assert "Couldn&#39;t find a channel" in html or "Couldn't find a channel" in html
    assert "Bake Lab" in html


def test_queue_page_removed(env):
    client, _ = env
    assert client.get("/queue").status_code == 404


def test_watch_renders_feed_with_open_video(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = ready(db, settings, c, "w", now - H)
    html = client.get(f"/watch/{v}").text
    assert f'data-open-video="{v}"' in html
    assert "Your feed" in html


def test_watch_expired_shows_unavailable_banner(env, settings, now):
    client, db = env
    c = make_channel(db, now)
    v = make_video(db, c, "e", now - H, status="expired")
    for url in (f"/watch/{v}", "/watch/9999"):
        html = client.get(url).text
        assert 'class="banner error"' in html
        assert "This video is no longer available." in html
        assert 'data-open-video=""' in html


def test_shell_has_single_video_and_player(env):
    client, _ = env
    html = client.get("/").text
    assert html.count("<video") == 1
    assert 'id="player"' in html and 'id="video"' in html


def test_no_third_party_hosts(env):
    client, _ = env
    for url in ("/", "/channels", "/static/css/app.css"):
        text = client.get(url).text
        for host in ("fonts.googleapis", "gstatic", "ytimg", "youtube.com"):
            assert host not in text, (url, host)


def test_fonts_served(env):
    client, _ = env
    r = client.get("/static/fonts/geist-latin-400-normal.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] in ("font/woff2", "application/octet-stream")


def test_static_assets_served(env):
    client, _ = env
    css = client.get("/static/css/app.css")
    assert css.status_code == 200 and "#1a1b26" in css.text
    for js in ("nav", "player", "feed"):
        assert client.get(f"/static/js/{js}.js").status_code == 200
    assert client.get("/static/manifest.webmanifest").status_code == 200
    assert client.get("/static/style.css").status_code == 404


def test_css_respects_hidden_attribute(env):
    """Elements with display rules (.toast, #player) must still hide via [hidden]."""
    client, _ = env
    css = client.get("/static/css/app.css").text.replace(" ", "")
    assert "[hidden]{display:none!important;}" in css


def test_footer_shows_version(env):
    from app import __version__
    client, _ = env
    assert f"v{__version__}" in client.get("/channels").text
