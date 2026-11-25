"""Fill a data dir with fake channels/videos for UI checks.

    python scripts/seed_demo.py /tmp/data           # 1-byte placeholder files
    python scripts/seed_demo.py /tmp/data --clips   # real 6 s H.264 clips + thumbnails (needs ffmpeg)

Matches the player e2e scenario (scripts/e2e_player.mjs): three ready videos a, b, c,
one downloading video d, and an empty queue. b is the newest, so it is the feed's poster.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import repo  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.feeds import FeedEntry  # noqa: E402
from app.youtube import ChannelInfo  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
clips = "--clips" in sys.argv[1:]
if len(args) != 1:
    sys.exit(__doc__)
data = Path(args[0])
s = load_settings({"DATA_DIR": str(data)})
for d in (s.videos_dir, s.thumbs_dir):
    d.mkdir(parents=True, exist_ok=True)
conn = connect(s.db_path)
init_db(conn)
now = int(time.time())
tech = repo.create_group(conn, "Tech")
cook = repo.create_group(conn, "Cooking")
chans = [
    repo.add_channel(conn, ChannelInfo("UCdemo1", "Bake Lab", None, "u"), cook, now),
    repo.add_channel(conn, ChannelInfo("UCdemo2", "Circuit Notes", None, "u"), tech, now),
    repo.add_channel(conn, ChannelInfo("UCdemo3", "Slow Travel", None, "u"), None, now),
]
# (key, channel, title, hours ago, duration, status, thumbnail hue)
rows = [
    ("a", 0, "How I finally fixed my sourdough starter", 3, 6, "ready", 0),
    ("b", 1, "Building a tiny e-ink weather station", 1, 6, "ready", 120),
    ("c", 1, "Why your USB-C cable is lying to you", 5, 6, "ready", 240),
    ("d", 2, "Three quiet days on the Norwegian coast", 7, 2711, "downloading", 60),
]


def ffmpeg(*argv: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *argv], check=True)


for key, c, title, hours, dur, status, hue in rows:
    yid = f"demo{key}{'0' * 6}"
    repo.upsert_video(conn, chans[c], FeedEntry(yid, title, now - hours * 3600, "", ""), status, now)
    v = repo.get_video_by_yid(conn, yid)["id"]
    repo.set_video_meta(conn, v, dur, status)
    if status != "ready":
        continue
    f = s.videos_dir / f"{yid}.mp4"
    if clips:
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=25",
            "-f", "lavfi", "-i", "sine=duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f),
        )
        thumb = s.thumbs_dir / f"{yid}.jpg"
        ffmpeg("-ss", "1", "-i", str(f), "-frames:v", "1", "-vf", f"hue=h={hue}", str(thumb))
        repo.set_thumbnail(conn, v, str(thumb))
    else:
        f.write_bytes(b"0")
    repo.set_status(conn, v, "ready", file_path=str(f))
repo.record_channel_check(conn, chans[2], now, "Feed request failed with HTTP 500")
print("seeded", data, "(clips)" if clips else "")
