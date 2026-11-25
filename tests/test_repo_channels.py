import pytest

from app import repo
from app.youtube import ChannelInfo


def info(cid="UCaaa", title="Chan A"):
    return ChannelInfo(channel_id=cid, title=title, handle="@a", url="https://www.youtube.com/@a")


def test_add_channel_twice_raises_already_added(conn, now):
    repo.add_channel(conn, info(), None, now)
    with pytest.raises(repo.AlreadyAdded):
        repo.add_channel(conn, info(), None, now)
    assert len(repo.list_channels(conn)) == 1


def test_delete_group_ungroups_channels(conn, now):
    g = repo.create_group(conn, "Tech")
    cid = repo.add_channel(conn, info(), g, now)
    assert repo.get_channel(conn, cid)["group_name"] == "Tech"
    repo.delete_group(conn, g)
    assert repo.get_channel(conn, cid)["group_id"] is None
    assert repo.list_groups(conn) == []


def test_rename_group_and_ordering(conn):
    b = repo.create_group(conn, "B")
    repo.create_group(conn, "A")
    repo.rename_group(conn, b, "Z")
    assert [g["name"] for g in repo.list_groups(conn)] == ["A", "Z"]


def test_delete_channel_cascades_videos_and_returns_paths(conn, now):
    cid = repo.add_channel(conn, info(), None, now)
    conn.execute(
        "INSERT INTO videos (youtube_video_id, channel_id, title, published_at, file_path, thumbnail_path)"
        " VALUES ('v1', ?, 't', ?, '/d/v/a.mp4', '/d/t/a.jpg')",
        (cid, now),
    )
    paths = repo.delete_channel(conn, cid)
    assert "/d/v/a.mp4" in paths and "/d/t/a.jpg" in paths
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
    assert repo.get_channel(conn, cid) is None


def test_record_channel_check_sets_error_and_clears_it(conn, now):
    cid = repo.add_channel(conn, info(), None, now)
    repo.record_channel_check(conn, cid, now, "boom")
    assert repo.get_channel(conn, cid)["last_error"] == "boom"
    repo.record_channel_check(conn, cid, now + 1, None)
    ch = repo.get_channel(conn, cid)
    assert ch["last_error"] is None and ch["last_checked_at"] == now + 1


def test_set_group_and_shorts(conn, now):
    g = repo.create_group(conn, "Tech")
    cid = repo.add_channel(conn, info(), None, now)
    repo.set_channel_group(conn, cid, g)
    repo.set_include_shorts(conn, cid, True)
    ch = repo.get_channel(conn, cid)
    assert ch["group_id"] == g and ch["include_shorts"] == 1
