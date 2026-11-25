from app import repo
from tests.helpers import H, make_channel, make_video


def qids(conn):
    return [r["id"] for r in repo.queue_list(conn)]


def test_queue_add_skips_duplicates_and_watched(conn, now):
    c = make_channel(conn, now)
    a = make_video(conn, c, "a", now - H)
    b = make_video(conn, c, "b", now - H)
    w = make_video(conn, c, "w", now - H)
    repo.mark_watched(conn, w, now)
    repo.queue_add(conn, [a, b])
    repo.queue_add(conn, [b, w, a])
    assert qids(conn) == [a, b]
    assert repo.feed(conn, "today", None, now)[0]["in_queue"] == 1


def test_queue_move_up_down_and_bounds(conn, now):
    c = make_channel(conn, now)
    a, b, d = (make_video(conn, c, x, now - H) for x in "abd")
    repo.queue_add(conn, [a, b, d])
    repo.queue_move(conn, a, "up")
    assert qids(conn) == [a, b, d]
    repo.queue_move(conn, d, "up")
    assert qids(conn) == [a, d, b]
    repo.queue_move(conn, a, "down")
    assert qids(conn) == [d, a, b]
    repo.queue_move(conn, b, "down")
    assert qids(conn) == [d, a, b]


def test_queue_remove(conn, now):
    c = make_channel(conn, now)
    a = make_video(conn, c, "a", now - H)
    repo.queue_add(conn, [a])
    repo.queue_remove(conn, a)
    assert qids(conn) == []


def test_queue_list_has_title_channel_status(conn, now):
    c = make_channel(conn, now, title="Chan A")
    a = make_video(conn, c, "a", now - H, title="Hello")
    repo.queue_add(conn, [a])
    r = repo.queue_list(conn)[0]
    assert (r["title"], r["channel_title"], r["status"]) == ("Hello", "Chan A", "ready")


def _ready(conn, c, yid, now):
    v = make_video(conn, c, yid, now - H)
    repo.set_status(conn, v, "ready", file_path=f"/x/{yid}.mp4")
    return v


def test_queue_current_skips_not_downloaded(conn, now):
    c = make_channel(conn, now)
    a = make_video(conn, c, "a", now - H, status="pending")
    b = _ready(conn, c, "b", now)
    repo.queue_add(conn, [a, b])
    assert repo.queue_current(conn)["id"] == b


def test_play_now_drops_playing_current(conn, now):
    c = make_channel(conn, now)
    a, b, d = (_ready(conn, c, x, now) for x in "abd")
    repo.queue_add(conn, [a, b])
    repo.save_progress(conn, a, 10, 100, now)
    repo.queue_play_now(conn, d, keep_current=False)
    assert qids(conn) == [d, b]
    row = repo.get_video(conn, a)
    assert row["watched_at"] is None and row["position_s"] == 10


def test_play_now_keeps_paused_current_as_next(conn, now):
    c = make_channel(conn, now)
    a, b, d = (_ready(conn, c, x, now) for x in "abd")
    repo.queue_add(conn, [a, b])
    repo.queue_play_now(conn, d, keep_current=True)
    assert qids(conn) == [d, a, b]


def test_play_now_on_already_queued_moves_it(conn, now):
    c = make_channel(conn, now)
    a, b, d = (_ready(conn, c, x, now) for x in "abd")
    repo.queue_add(conn, [a, b, d])
    repo.queue_play_now(conn, d, keep_current=True)
    assert qids(conn) == [d, a, b]


def test_play_next_after_current(conn, now):
    c = make_channel(conn, now)
    a, b, d = (_ready(conn, c, x, now) for x in "abd")
    repo.queue_add(conn, [a, b])
    repo.queue_play_next(conn, d)
    assert qids(conn) == [a, d, b]
    repo.queue_play_next(conn, b)
    assert qids(conn) == [a, b, d]


def test_play_next_empty_queue(conn, now):
    c = make_channel(conn, now)
    d = _ready(conn, c, "d", now)
    repo.queue_play_next(conn, d)
    assert qids(conn) == [d]


def test_play_next_ignores_watched(conn, now):
    c = make_channel(conn, now)
    a, w = (_ready(conn, c, x, now) for x in "aw")
    repo.queue_add(conn, [a])
    repo.mark_watched(conn, w, now)
    repo.queue_play_next(conn, w)
    assert qids(conn) == [a]


def test_queue_insert_at_clamps(conn, now):
    c = make_channel(conn, now)
    a, b, d, e = (_ready(conn, c, x, now) for x in "abde")
    repo.queue_add(conn, [a, b])
    repo.queue_insert_at(conn, d, 99)
    assert qids(conn) == [a, b, d]
    repo.queue_insert_at(conn, e, 0)
    assert qids(conn) == [e, a, b, d]


def test_save_progress_watched_keeps_in_queue(conn, now):
    c = make_channel(conn, now)
    a = _ready(conn, c, "a", now)
    repo.queue_add(conn, [a])
    assert repo.save_progress(conn, a, 95, 100, now) is True
    assert repo.get_video(conn, a)["watched_at"] is not None
    assert qids(conn) == [a]


def test_play_now_on_current_behind_downloading_pins_it_to_head(conn, now):
    # The player pins the item it loaded to the head (POST /api/queue/play keep_current) so a
    # downloading item ahead of it that finishes later can't become "current" and yank playback.
    c = make_channel(conn, now)
    d = make_video(conn, c, "d", now - H, status="downloading")
    b = _ready(conn, c, "b", now)
    repo.queue_add(conn, [d, b])
    repo.queue_play_now(conn, b, keep_current=True)
    assert qids(conn) == [b, d]
    repo.set_status(conn, d, "ready", file_path="/x/d.mp4")
    assert repo.queue_current(conn)["id"] == b
