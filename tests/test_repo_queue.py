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
