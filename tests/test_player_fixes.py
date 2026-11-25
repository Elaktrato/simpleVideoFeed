"""Regression tests for the player-redesign final fix wave (server/template side)."""
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import repo
from tests.helpers import H, make_channel, make_video
from tests.test_pages import env, ready  # noqa: F401  (fixture)

ROOT = Path(__file__).parents[1]


def _next_buttons(html, vid):
    return re.findall(rf'data-action="next" data-id="{vid}"', html)


def test_play_next_offered_for_downloading_cards_but_not_play(env, settings, now):  # noqa: F811
    client, db = env
    c = make_channel(db, now)
    ready(db, settings, c, "r", now - 5 * H)
    dl = make_video(db, c, "dl", now - 3 * H, status="downloading")
    pe = make_video(db, c, "pe", now - 2 * H, status="pending")
    poster = make_video(db, c, "po", now - 1 * H, status="pending")  # newest -> poster
    html = client.get("/").text
    for vid in (dl, pe, poster):
        assert _next_buttons(html, vid), f"no Play next for {vid}"
        assert f'data-action="play" data-id="{vid}"' not in html


def test_play_next_not_offered_for_failed_cards(env, settings, now):  # noqa: F811
    client, db = env
    c = make_channel(db, now)
    ready(db, settings, c, "r", now - H)
    f = make_video(db, c, "f", now - 2 * H, status="pending")
    repo.set_status(db, f, "failed", error="boom")
    assert not _next_buttons(client.get("/").text, f)


def test_sheet_open_disables_pull_to_refresh(env):  # noqa: F811
    client, _ = env
    css = client.get("/static/css/app.css").text
    rule = re.search(r"html\.sheet-open\s*\{([^}]*)\}", css)
    assert rule and "overscroll-behavior-y: none" in rule.group(1)


def test_seed_demo_has_failed_and_pending_rows_outside_the_queue(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py"), str(tmp_path)], check=True,
                   capture_output=True)
    db = sqlite3.connect(tmp_path / "feed.db")
    statuses = sorted(r[0] for r in db.execute("SELECT status FROM videos"))
    assert statuses.count("failed") == 1 and statuses.count("pending") == 1
    assert statuses.count("ready") == 3 and statuses.count("downloading") == 1
    assert db.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
