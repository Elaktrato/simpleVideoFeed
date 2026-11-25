import httpx
import pytest

from app import youtube


class FakeYDL:
    info = {}
    error = None
    last_opts = None
    last_url = None

    def __init__(self, opts):
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False, process=True):
        FakeYDL.last_url = url
        if FakeYDL.error:
            raise FakeYDL.error
        return FakeYDL.info


@pytest.fixture
def ydl(monkeypatch):
    FakeYDL.info, FakeYDL.error, FakeYDL.last_opts = {}, None, None
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


@pytest.fixture
def http(monkeypatch):
    routes = {}

    def handler(request):
        fn = routes.get(str(request.url))
        if fn is None:
            return httpx.Response(404)
        return fn(request)

    monkeypatch.setattr(youtube, "_transport", httpx.MockTransport(handler))
    return routes


def test_ydl_download_opts_720_h264_mp4(tmp_path):
    o = youtube.ydl_download_opts("abc", tmp_path, 720, None)
    assert o["format"] == (
        "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/b[height<=720][ext=mp4]"
        "/bv*[height<=720]+ba/b[height<=720]"
    )
    assert o["merge_output_format"] == "mp4"
    assert o["outtmpl"] == str(tmp_path / "%(id)s.%(ext)s")
    assert "cookiefile" not in o


def test_ydl_download_opts_includes_cookies_when_set(tmp_path):
    assert youtube.ydl_download_opts("abc", tmp_path, 480, "/c.txt")["cookiefile"] == "/c.txt"


def test_resolve_channel_reads_channel_id(ydl):
    ydl.info = {"channel_id": "UCxyz", "channel": "Bake Lab", "uploader_id": "@bakelab",
                "channel_url": "https://www.youtube.com/channel/UCxyz"}
    info = youtube.resolve_channel("https://www.youtube.com/@bakelab", None)
    assert info.channel_id == "UCxyz"
    assert info.title == "Bake Lab"
    assert info.handle == "@bakelab"


def test_resolve_channel_falls_back_to_id_field(ydl):
    ydl.info = {"id": "UCqqq", "title": "Some Channel - Videos"}
    assert youtube.resolve_channel("https://www.youtube.com/channel/UCqqq", None).channel_id == "UCqqq"


def test_resolve_channel_without_channel_id_raises(ydl):
    ydl.info = {"id": "PLabc", "title": "A playlist"}
    with pytest.raises(youtube.YouTubeError, match="Couldn't find a channel"):
        youtube.resolve_channel("https://www.youtube.com/playlist?list=PLabc", None)


def test_resolve_channel_error_becomes_youtube_error(ydl):
    ydl.error = youtube.yt_dlp.utils.DownloadError("nope")
    with pytest.raises(youtube.YouTubeError, match="Couldn't find a channel"):
        youtube.resolve_channel("https://www.youtube.com/@doesnotexist", None)


def test_resolve_channel_rejects_non_youtube_url(ydl):
    with pytest.raises(youtube.YouTubeError):
        youtube.resolve_channel("not a url", None)


def test_fetch_feed_returns_text(http):
    http["https://example.test/feed"] = lambda r: httpx.Response(200, text="<feed/>")
    assert youtube.fetch_feed("https://example.test/feed") == "<feed/>"


def test_fetch_feed_non_200_raises(http):
    with pytest.raises(youtube.YouTubeError, match="404"):
        youtube.fetch_feed("https://example.test/missing")


def test_is_short(http):
    http["https://www.youtube.com/shorts/S"] = lambda r: httpx.Response(200)
    http["https://www.youtube.com/shorts/L"] = lambda r: httpx.Response(
        303, headers={"location": "https://www.youtube.com/watch?v=L"})
    assert youtube.is_short("S") is True
    assert youtube.is_short("L") is False


def test_fetch_meta(ydl):
    ydl.info = {"duration": 754.2, "live_status": "not_live"}
    m = youtube.fetch_meta("abc", None)
    assert m.duration_s == 754 and m.live_status == "not_live"


def test_download_video_success(ydl, tmp_path):
    (tmp_path / "abc.mp4").write_bytes(b"x")
    assert youtube.download_video("abc", tmp_path, 720, None) == tmp_path / "abc.mp4"
    assert ydl.last_url == "https://www.youtube.com/watch?v=abc"


def test_download_video_failure_removes_part_files(ydl, tmp_path):
    (tmp_path / "abc.f137.mp4.part").write_bytes(b"x")
    (tmp_path / "other.mp4").write_bytes(b"keep")
    ydl.error = youtube.yt_dlp.utils.DownloadError("Sign in to confirm you're not a bot")
    with pytest.raises(youtube.YouTubeError, match="not a bot"):
        youtube.download_video("abc", tmp_path, 720, None)
    assert not list(tmp_path.glob("abc*"))
    assert (tmp_path / "other.mp4").exists()


def test_download_thumbnail(http, tmp_path):
    http["https://i.ytimg.com/vi/a/hq.jpg"] = lambda r: httpx.Response(200, content=b"JPG")
    dest = tmp_path / "a.jpg"
    youtube.download_thumbnail("https://i.ytimg.com/vi/a/hq.jpg", dest)
    assert dest.read_bytes() == b"JPG"
