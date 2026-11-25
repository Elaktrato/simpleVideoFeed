from app import repo
from app.feeds import FeedEntry
from tests.helpers import H, make_channel, make_video


def ids(rows):
    return [r["youtube_video_id"] for r in rows]


def test_today_vs_week_windows(conn, now):
    c = make_channel(conn, now)
    make_video(conn, c, "old", now - 8 * 24 * H)
    make_video(conn, c, "h30", now - 30 * H)
    make_video(conn, c, "h2", now - 2 * H)
    assert ids(repo.feed(conn, "today", None, now)) == ["h2"]
    assert ids(repo.feed(conn, "week", None, now)) == ["h2", "h30"]


def test_feed_includes_pending_downloading_failed(conn, now):
    c = make_channel(conn, now)
    for i, st in enumerate(["pending", "downloading", "failed", "ready"]):
        make_video(conn, c, st, now - i * 60, status=st)
    assert set(ids(repo.feed(conn, "today", None, now))) == {"pending", "downloading", "failed", "ready"}


def test_watched_and_skipped_hidden(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "w", now - H)
    make_video(conn, c, "s", now - H, status="skipped")
    make_video(conn, c, "wt", now - H, status="waiting")
    make_video(conn, c, "e", now - H, status="expired")
    make_video(conn, c, "keep", now - H)
    repo.mark_watched(conn, v, now)
    assert ids(repo.feed(conn, "week", None, now)) == ["keep"]


def test_group_filter(conn, now):
    g = repo.create_group(conn, "Tech")
    a = make_channel(conn, now, "UCa", "A", group_id=g)
    b = make_channel(conn, now, "UCb", "B")
    make_video(conn, a, "va", now - H)
    make_video(conn, b, "vb", now - H)
    assert ids(repo.feed(conn, "today", g, now)) == ["va"]
    assert set(ids(repo.feed(conn, "today", None, now))) == {"va", "vb"}
    assert repo.feed(conn, "today", None, now)[0]["channel_title"] in ("A", "B")


def test_feed_counts(conn, now):
    c = make_channel(conn, now)
    make_video(conn, c, "a", now - H)
    make_video(conn, c, "b", now - 30 * H)
    assert repo.feed_counts(conn, None, now) == {"today": 1, "week": 2}


def test_upsert_same_video_twice_is_noop(conn, now):
    c = make_channel(conn, now)
    e = FeedEntry("x", "T", now, "u", "l")
    assert repo.upsert_video(conn, c, e, "pending", now) is True
    v = repo.get_video_by_yid(conn, "x")["id"]
    repo.set_status(conn, v, "ready", file_path="/f.mp4")
    assert repo.upsert_video(conn, c, e, "pending", now) is False
    row = repo.get_video(conn, v)
    assert row["status"] == "ready" and row["file_path"] == "/f.mp4"


def test_save_progress_marks_watched_at_90_percent(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "p", now - H)
    assert repo.save_progress(conn, v, 89, 100, now) is False
    row = repo.get_video(conn, v)
    assert row["position_s"] == 89 and row["watched_at"] is None
    assert repo.save_progress(conn, v, 90, 100, now) is True
    assert repo.get_video(conn, v)["watched_at"] == now


def test_save_progress_zero_duration_never_watches(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "z", now - H)
    assert repo.save_progress(conn, v, 0, 0, now) is False


def test_mark_watched_removes_from_queue(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "q", now - H)
    repo.queue_add(conn, [v])
    repo.mark_watched(conn, v, now)
    assert repo.queue_list(conn) == []


def test_unmark_watched_restores_to_feed(conn, now):
    c = make_channel(conn, now)
    v = make_video(conn, c, "u", now - H)
    repo.mark_watched(conn, v, now)
    repo.unmark_watched(conn, v)
    assert ids(repo.feed(conn, "today", None, now)) == ["u"]


def test_next_pending_respects_attempts(conn, now):
    c = make_channel(conn, now)
    a = make_video(conn, c, "a", now - 2 * H, status="pending")
    b = make_video(conn, c, "b", now - H, status="pending")
    for _ in range(3):
        repo.bump_attempts(conn, a)
    assert repo.claim_next_pending(conn, now)["id"] == b


def test_claim_next_pending_oldest_first(conn, now):
    c = make_channel(conn, now)
    a = make_video(conn, c, "a", now - 2 * H, status="pending")
    make_video(conn, c, "b", now - H, status="pending")
    assert repo.claim_next_pending(conn, now)["id"] == a
