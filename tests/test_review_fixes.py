"""Regression tests for findings from the final review."""
from pathlib import Path

import pytest

from app import jobs, repo, youtube
from app.db import connect, init_db
from app.youtube import YouTubeError
from tests.fakeyt import FakeYT
from tests.helpers import H, make_channel, make_video
from tests.test_youtube import FakeYDL, ydl  # noqa: F401  (fixture)


def test_requirements_include_js_challenge_solver():
    req = (Path(__file__).parents[1] / "requirements.txt").read_text()
    assert "yt-dlp[default,deno]" in req


def test_fetch_meta_tolerates_upcoming_without_formats(ydl):  # noqa: F811
    ydl.info = {"live_status": "is_upcoming"}
    assert youtube.fetch_meta("abc", None).live_status == "is_upcoming"
    assert ydl.last_opts["ignore_no_formats_error"] is True
    assert ydl.last_opts["noplaylist"] is True


def test_resolve_channel_uses_noplaylist(ydl):  # noqa: F811
    ydl.info = {"channel_id": "UCvid", "channel": "Video Owner"}
    youtube.resolve_channel("https://www.youtube.com/watch?v=X&list=PLcurator", None)
    assert ydl.last_opts["noplaylist"] is True


def test_resolve_channel_rejects_playlist_links(ydl):  # noqa: F811
    ydl.info = {"channel_id": "UCcurator", "channel": "Curator", "_type": "playlist", "id": "PLx"}
    with pytest.raises(YouTubeError, match="playlist"):
        youtube.resolve_channel("https://www.youtube.com/playlist?list=PLx", None)


def test_download_opts_remux_to_mp4_and_skip_live(tmp_path):
    o = youtube.ydl_download_opts("abc", tmp_path, 720, None)
    assert {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"} in o["postprocessors"]
    assert o["match_filter"]({"is_live": True}, incomplete=False) is not None
    assert o["match_filter"]({"is_live": False}, incomplete=False) is None


def test_failed_download_waits_before_retry(conn, settings, now):
    yt = FakeYT()
    c = make_channel(conn, now)
    v = make_video(conn, c, "f", now - H, status="pending")
    yt.download_errors["f"] = [YouTubeError("blip")]
    assert jobs.download_one(conn, settings, yt, now) is True
    assert repo.get_video(conn, v)["status"] == "pending"
    # Immediately afterwards nothing is eligible...
    assert jobs.download_one(conn, settings, yt, now + 60) is False
    # ...but after the backoff it is retried and succeeds.
    assert jobs.download_one(conn, settings, yt, now + 11 * 60) is True
    assert repo.get_video(conn, v)["status"] == "ready"


def test_backoff_grows_with_attempts(conn, settings, now):
    yt = FakeYT()
    c = make_channel(conn, now)
    v = make_video(conn, c, "g", now - H, status="pending")
    yt.download_errors["g"] = [YouTubeError("x"), YouTubeError("y")]
    jobs.download_one(conn, settings, yt, now)
    first = repo.get_video(conn, v)["next_attempt_at"]
    jobs.download_one(conn, settings, yt, first)
    second = repo.get_video(conn, v)["next_attempt_at"]
    assert first - now == 600 and second - first == 1200


def test_claim_next_pending_is_exclusive(settings, now):
    a = connect(settings.db_path)
    init_db(a)
    b = connect(settings.db_path)
    c = make_channel(a, now)
    make_video(a, c, "one", now - H, status="pending")
    first = repo.claim_next_pending(a, now)
    second = repo.claim_next_pending(b, now)
    assert first["youtube_video_id"] == "one" and first["status"] == "downloading"
    assert second is None
    a.close()
    b.close()


def test_retry_endpoint_clears_backoff(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "r", now - H, status="failed")
    conn.execute("UPDATE videos SET next_attempt_at = ? WHERE id = ?", (now + 9999, v))
    conn.commit()
    repo.reset_attempts(conn, v)
    repo.set_status(conn, v, "pending")
    assert repo.claim_next_pending(conn, now)["id"] == v
