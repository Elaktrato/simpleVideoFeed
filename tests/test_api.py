import pytest
from fastapi.testclient import TestClient

from app import repo
from app.db import connect
from app.feeds import feed_url
from app.main import create_app
from tests.fakeyt import FakeYT, channel_info, feed_xml
from tests.helpers import H, make_channel, make_video


@pytest.fixture
def env(settings, now):
    yt = FakeYT()
    app = create_app(settings, yt=yt, start_background=False, clock=lambda: now)
    with TestClient(app) as client:
        db = connect(settings.db_path)
        yield client, db, yt
        db.close()


def ready_video(db, settings, now, yid="v1"):
    c = repo.get_channel(db, 1)["id"] if repo.get_channel(db, 1) else make_channel(db, now)
    v = make_video(db, c, yid, now - H, status="pending")
    f = settings.videos_dir / f"{yid}.mp4"
    f.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    repo.set_status(db, v, "ready", file_path=str(f))
    return v


def test_healthz(env):
    client, _, _ = env
    assert client.get("/healthz").json() == {"ok": True}


def test_progress_marks_watched_at_90(env, settings, now):
    client, db, _ = env
    v = ready_video(db, settings, now)
    assert client.post(f"/api/videos/{v}/progress", json={"position": 50, "duration": 100}).json() == {"watched": False}
    assert repo.get_video(db, v)["position_s"] == 50
    assert client.post(f"/api/videos/{v}/progress", json={"position": 95, "duration": 100}).json() == {"watched": True}
    assert repo.get_video(db, v)["watched_at"] == now


def test_progress_unknown_video_404(env):
    client, _, _ = env
    r = client.post("/api/videos/999/progress", json={"position": 1, "duration": 2})
    assert r.status_code == 404 and "error" in r.json()


def test_watched_then_undo(env, settings, now):
    client, db, _ = env
    v = ready_video(db, settings, now)
    assert client.post(f"/api/videos/{v}/watched").status_code == 200
    assert repo.get_video(db, v)["watched_at"] == now
    assert client.delete(f"/api/videos/{v}/watched").status_code == 200
    assert repo.get_video(db, v)["watched_at"] is None


def test_queue_add_list_move_delete(env, settings, now):
    client, db, _ = env
    a = ready_video(db, settings, now, "a")
    b = ready_video(db, settings, now, "b")
    client.post("/api/queue", json={"video_ids": [a, b]})
    q = client.get("/api/queue").json()
    assert [x["id"] for x in q] == [a, b]
    assert q[0]["src"] == "/media/a.mp4" and q[0]["channel"] == "Chan A" and q[0]["status"] == "ready"
    client.post(f"/api/queue/{b}/move", json={"direction": "up"})
    assert [x["id"] for x in client.get("/api/queue").json()] == [b, a]
    client.delete(f"/api/queue/{b}")
    assert [x["id"] for x in client.get("/api/queue").json()] == [a]


def test_queue_move_bad_direction_400(env, settings, now):
    client, db, _ = env
    a = ready_video(db, settings, now, "a")
    client.post("/api/queue", json={"video_ids": [a]})
    assert client.post(f"/api/queue/{a}/move", json={"direction": "sideways"}).status_code == 400


def test_media_supports_range(env, settings, now):
    client, db, _ = env
    ready_video(db, settings, now, "rng")
    r = client.get("/media/rng.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100
    assert r.headers["content-type"] == "video/mp4"


def test_media_missing_404(env):
    client, _, _ = env
    assert client.get("/media/nope.mp4").status_code == 404
    assert client.get("/thumbs/nope.jpg").status_code == 404


def test_media_rejects_path_traversal(env):
    client, _, _ = env
    assert client.get("/media/..%2Ffeed.db.mp4").status_code == 404


def test_add_channel_bad_url_redirects_with_error_and_saves_nothing(env):
    client, db, _ = env
    r = client.post("/channels", data={"url": "https://www.youtube.com/@nope", "group_id": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/channels?error=")
    assert repo.list_channels(db) == []


def test_add_channel_polls_in_background(env, now):
    client, db, yt = env
    yt.channels["https://www.youtube.com/@a"] = channel_info("UCaaa", "Chan A")
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([("n1", now - H, None)])
    g = repo.create_group(db, "Tech")
    r = client.post("/channels", data={"url": "https://www.youtube.com/@a", "group_id": str(g)}, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    ch = repo.list_channels(db)[0]
    assert ch["group_id"] == g
    assert repo.get_video_by_yid(db, "n1") is not None


def test_add_channel_twice_shows_already_added(env):
    client, db, yt = env
    yt.channels["https://www.youtube.com/@a"] = channel_info()
    client.post("/channels", data={"url": "https://www.youtube.com/@a", "group_id": ""})
    r = client.post("/channels", data={"url": "https://www.youtube.com/@a", "group_id": ""}, follow_redirects=False)
    assert "already" in r.headers["location"]
    assert len(repo.list_channels(db)) == 1


def test_delete_channel_unlinks_files(env, settings, now):
    client, db, _ = env
    v = ready_video(db, settings, now, "del")
    f = settings.videos_dir / "del.mp4"
    cid = repo.get_video(db, v)["channel_id"]
    client.post(f"/channels/{cid}/delete")
    assert not f.exists()
    assert repo.list_channels(db) == []


def test_channel_group_and_shorts_forms(env, now):
    client, db, _ = env
    c = make_channel(db, now)
    g = repo.create_group(db, "Tech")
    client.post(f"/channels/{c}/group", data={"group_id": str(g)})
    client.post(f"/channels/{c}/shorts", data={"include": "1"})
    ch = repo.get_channel(db, c)
    assert ch["group_id"] == g and ch["include_shorts"] == 1
    client.post(f"/channels/{c}/group", data={"group_id": ""})
    client.post(f"/channels/{c}/shorts", data={})
    ch = repo.get_channel(db, c)
    assert ch["group_id"] is None and ch["include_shorts"] == 0


def test_group_forms(env):
    client, db, _ = env
    client.post("/groups", data={"name": "Tech"})
    g = repo.list_groups(db)[0]["id"]
    client.post(f"/groups/{g}/rename", data={"name": "Science"})
    assert repo.list_groups(db)[0]["name"] == "Science"
    client.post(f"/groups/{g}/delete")
    assert repo.list_groups(db) == []


def test_empty_group_name_rejected(env):
    client, db, _ = env
    r = client.post("/groups", data={"name": "  "}, follow_redirects=False)
    assert "error" in r.headers["location"]
    assert repo.list_groups(db) == []


def test_retry_failed_resets_to_pending(env, settings, now):
    client, db, _ = env
    c = make_channel(db, now)
    v = make_video(db, c, "f", now - H, status="failed")
    for _ in range(3):
        repo.bump_attempts(db, v)
    client.post(f"/api/videos/{v}/retry")
    row = repo.get_video(db, v)
    assert row["status"] == "pending" and row["attempts"] == 0
