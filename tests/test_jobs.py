from pathlib import Path

from app import jobs, repo
from app.feeds import feed_url
from app.youtube import VideoMeta, YouTubeError
from tests.fakeyt import FakeYT, channel_info, feed_xml
from tests.helpers import H, make_channel, make_video

D = 24 * H


def setup_channel(conn, now, cid="UCaaa"):
    c = repo.add_channel(conn, channel_info(cid), None, now)
    return repo.get_channel(conn, c)


def statuses(conn):
    return {r["youtube_video_id"]: r["status"] for r in conn.execute("SELECT * FROM videos")}


def test_poll_inserts_new_pending_and_skips_old(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([("new", now - H, None), ("old", now - 8 * D, None)])
    assert jobs.poll_channel(conn, settings, ch, yt, now) == 1
    assert statuses(conn) == {"new": "pending"}
    v = repo.get_video_by_yid(conn, "new")
    assert v["duration_s"] == 600
    assert Path(v["thumbnail_path"]).read_bytes() == b"JPG"
    assert repo.get_channel(conn, ch["id"])["last_checked_at"] == now
    # second poll adds nothing
    assert jobs.poll_channel(conn, settings, ch, yt, now) == 0


def test_poll_uses_long_form_feed_when_shorts_excluded(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([])
    jobs.poll_channel(conn, settings, ch, yt, now)
    assert "UULF" in yt.requested[0]


def test_poll_uses_full_feed_when_shorts_included(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    repo.set_include_shorts(conn, ch["id"], True)
    ch = repo.get_channel(conn, ch["id"])
    yt.feeds[feed_url("UCaaa", False)] = feed_xml([("s1", now - H, "https://www.youtube.com/shorts/s1")])
    jobs.poll_channel(conn, settings, ch, yt, now)
    assert "channel_id=UCaaa" in yt.requested[0]
    assert statuses(conn) == {"s1": "pending"}


def test_poll_falls_back_and_skips_shorts_on_404(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    yt.feeds[feed_url("UCaaa", False)] = feed_xml(
        [("long", now - H, None), ("byurl", now - H, "https://www.youtube.com/shorts/byurl"), ("byhead", now - H, None)]
    )
    yt.shorts.add("byhead")
    assert jobs.poll_channel(conn, settings, ch, yt, now) == 3
    assert statuses(conn) == {"long": "pending", "byurl": "skipped", "byhead": "skipped"}
    assert repo.get_channel(conn, ch["id"])["last_error"] is None


def test_poll_live_waiting_then_skipped_when_was_live(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([("live", now - H, None), ("soon", now - H, None)])
    yt.meta["live"] = VideoMeta(None, "is_live")
    yt.meta["soon"] = VideoMeta(None, "is_upcoming")
    jobs.poll_channel(conn, settings, ch, yt, now)
    assert statuses(conn) == {"live": "waiting", "soon": "waiting"}
    yt.meta["live"] = VideoMeta(3600, "was_live")
    yt.meta["soon"] = VideoMeta(900, "not_live")
    jobs.poll_channel(conn, settings, ch, yt, now)
    assert statuses(conn) == {"live": "skipped", "soon": "pending"}
    assert repo.get_video_by_yid(conn, "soon")["duration_s"] == 900


def test_poll_meta_error_leaves_video_pending(conn, settings, now):
    yt = FakeYT()
    ch = setup_channel(conn, now)
    yt.feeds[feed_url("UCaaa", True)] = feed_xml([("x", now - H, None)])
    yt.meta["x"] = YouTubeError("bot check")
    jobs.poll_channel(conn, settings, ch, yt, now)
    assert statuses(conn) == {"x": "pending"}


def test_poll_error_recorded_other_channels_continue(conn, settings, now):
    yt = FakeYT()
    setup_channel(conn, now, "UCbad")
    setup_channel(conn, now, "UCgood")
    yt.feeds[feed_url("UCbad", True)] = YouTubeError("Couldn't reach YouTube")
    yt.feeds[feed_url("UCgood", True)] = feed_xml([("g", now - H, None)])
    jobs.poll_all(conn, settings, yt, now)
    assert statuses(conn) == {"g": "pending"}
    bad = [c for c in repo.list_channels(conn) if c["youtube_channel_id"] == "UCbad"][0]
    assert "Couldn't reach YouTube" in bad["last_error"]


def test_poll_all_survives_unexpected_exception(conn, settings, now):
    yt = FakeYT()
    setup_channel(conn, now, "UCbad")
    setup_channel(conn, now, "UCgood")
    yt.feeds[feed_url("UCbad", True)] = RuntimeError("weird")
    yt.feeds[feed_url("UCgood", True)] = feed_xml([("g", now - H, None)])
    jobs.poll_all(conn, settings, yt, now)
    assert statuses(conn) == {"g": "pending"}


def test_download_success_sets_ready(conn, settings, now):
    yt = FakeYT()
    c = make_channel(conn, now)
    v = make_video(conn, c, "d", now - H, status="pending")
    assert jobs.download_one(conn, settings, yt, now) is True
    row = repo.get_video(conn, v)
    assert row["status"] == "ready"
    assert row["file_path"] == str(settings.videos_dir / "d.mp4")
    assert jobs.download_one(conn, settings, yt, now) is False


def test_download_retries_then_failed(conn, settings, now):
    yt = FakeYT()
    c = make_channel(conn, now)
    v = make_video(conn, c, "f", now - H, status="pending")
    yt.download_errors["f"] = [YouTubeError("bot check") for _ in range(3)]
    t = now
    for expected in ("pending", "pending", "failed"):
        jobs.download_one(conn, settings, yt, t)
        assert repo.get_video(conn, v)["status"] == expected
        t = repo.get_video(conn, v)["next_attempt_at"]
    assert repo.get_video(conn, v)["error"] == "bot check"
    assert jobs.download_one(conn, settings, yt, t + 10**6) is False


def test_cleanup_watched_after_grace(conn, settings, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "w", now - 2 * D)
    f = settings.videos_dir / "w.mp4"
    f.write_bytes(b"x")
    repo.set_status(conn, v, "ready", file_path=str(f))
    repo.mark_watched(conn, v, now - 23 * H)
    assert jobs.cleanup(conn, settings, now) == 0 and f.exists()
    assert jobs.cleanup(conn, settings, now + 2 * H) == 1 and not f.exists()
    assert repo.get_video(conn, v)["file_path"] is None


def test_cleanup_expires_after_retention_and_removes_from_queue(conn, settings, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "e", now - 15 * D)
    keep = make_video(conn, c, "k", now - 13 * D)
    f = settings.videos_dir / "e.mp4"
    f.write_bytes(b"x")
    t = settings.thumbs_dir / "e.jpg"
    t.write_bytes(b"x")
    repo.set_status(conn, v, "ready", file_path=str(f))
    repo.set_thumbnail(conn, v, str(t))
    repo.queue_add(conn, [v, keep])
    jobs.cleanup(conn, settings, now)
    assert not f.exists() and not t.exists()
    assert repo.get_video(conn, v)["status"] == "expired"
    assert repo.get_video(conn, keep)["status"] == "ready"
    assert [r["id"] for r in repo.queue_list(conn)] == [keep]


def test_cleanup_tolerates_missing_files(conn, settings, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "m", now - 15 * D)
    repo.set_status(conn, v, "ready", file_path=str(settings.videos_dir / "gone.mp4"))
    jobs.cleanup(conn, settings, now)
    assert repo.get_video(conn, v)["status"] == "expired"


def test_recover_resets_downloading_and_removes_part_files(conn, settings, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "r", now - H, status="downloading")
    part = settings.videos_dir / "r.f137.mp4.part"
    part.write_bytes(b"x")
    done = settings.videos_dir / "other.mp4"
    done.write_bytes(b"x")
    jobs.recover(conn, settings)
    assert repo.get_video(conn, v)["status"] == "pending"
    assert not part.exists() and done.exists()


def test_cleanup_watched_grace_also_dequeues(conn, settings, now):
    # 90% marks the current item watched but keeps it queued; once its file is cleaned up it
    # can never play, so it must leave the queue too (or the bar shows it forever).
    c = make_channel(conn, now)
    v = make_video(conn, c, "q", now - 2 * D)
    other = make_video(conn, c, "o", now - 2 * D)
    f = settings.videos_dir / "q.mp4"
    f.write_bytes(b"x")
    repo.set_status(conn, v, "ready", file_path=str(f))
    repo.queue_add(conn, [v, other])
    assert repo.save_progress(conn, v, 95, 100, now - 25 * H)
    assert [r["id"] for r in repo.queue_list(conn)] == [v, other]
    assert jobs.cleanup(conn, settings, now) == 1
    assert repo.get_video(conn, v)["file_path"] is None
    assert [r["id"] for r in repo.queue_list(conn)] == [other]
