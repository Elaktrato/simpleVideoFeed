from app import repo
from app.feeds import FeedEntry
from app.youtube import ChannelInfo

H = 3600


def make_channel(conn, now, cid="UCaaa", title="Chan A", group_id=None):
    return repo.add_channel(
        conn, ChannelInfo(cid, title, None, f"https://www.youtube.com/channel/{cid}"), group_id, now
    )


def make_video(conn, channel_id, vid, published_at, status="ready", title=None):
    e = FeedEntry(
        video_id=vid,
        title=title or f"Video {vid}",
        published_at=published_at,
        thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )
    repo.upsert_video(conn, channel_id, e, status, published_at)
    return repo.get_video_by_yid(conn, vid)["id"]
