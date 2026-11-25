from datetime import datetime, timezone
from pathlib import Path

from app.feeds import feed_url, is_short_url, parse_feed

FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_text()


def test_feed_url_long_form():
    assert feed_url("UCabc", True) == "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFabc"


def test_feed_url_all():
    assert feed_url("UCabc", False) == "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"


def test_parse_feed_fixture():
    entries = parse_feed(FIXTURE)
    assert len(entries) == 2
    e = entries[0]
    assert e.video_id == "AAAAAAAAAAA"
    assert e.title == "How I fixed my sourdough"
    assert e.published_at == int(datetime(2026, 11, 24, 10, tzinfo=timezone.utc).timestamp())
    assert e.thumbnail_url == "https://i4.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"
    assert e.url == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert entries[1].url.endswith("/shorts/BBBBBBBBBBB")


def test_parse_feed_empty_feed_returns_empty_list():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
    assert parse_feed(xml) == []


def test_parse_feed_garbage_returns_empty_list():
    assert parse_feed("<html>not a feed") == []


def test_is_short_url():
    assert is_short_url("https://www.youtube.com/shorts/BBB")
    assert not is_short_url("https://www.youtube.com/watch?v=AAA")
