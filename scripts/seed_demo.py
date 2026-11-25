"""Fill a data dir with fake channels/videos for UI checks: python scripts/seed_demo.py /tmp/data"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import repo  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.feeds import FeedEntry  # noqa: E402
from app.youtube import ChannelInfo  # noqa: E402

data = Path(sys.argv[1])
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
rows = [
    (0, "How I finally fixed my sourdough starter", 3, 842, "ready"),
    (1, "Building a tiny e-ink weather station", 7, 1520, "ready"),
    (2, "Three quiet days on the Norwegian coast", 20, 2711, "downloading"),
    (1, "Why your USB-C cable is lying to you", 40, 655, "ready"),
    (0, "Laminated dough without a sheeter", 80, 1204, "failed"),
    (2, "Night train to Vienna", 120, 3901, "pending"),
]
for i, (c, title, hours, dur, status) in enumerate(rows):
    vid = f"demo{i:07d}"
    repo.upsert_video(conn, chans[c], FeedEntry(vid, title, now - hours * 3600, "", ""), status, now)
    v = repo.get_video_by_yid(conn, vid)["id"]
    repo.set_video_meta(conn, v, dur, status)
    if status == "ready":
        f = s.videos_dir / f"{vid}.mp4"
        f.write_bytes(b"0")
        repo.set_status(conn, v, "ready", file_path=str(f))
repo.queue_add(conn, [1])
repo.record_channel_check(conn, chans[2], now, "Feed request failed with HTTP 500")
print("seeded", data)
