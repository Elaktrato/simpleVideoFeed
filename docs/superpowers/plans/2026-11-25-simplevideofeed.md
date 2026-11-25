# simpleVideoFeed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted, single-user web app that polls chosen YouTube channels, downloads their new uploads, and shows a finite Today / This Week feed with a server-side queue.

**Architecture:** One Python process: FastAPI serves Jinja2 pages, a small JSON API and the video files. Background asyncio loops poll RSS feeds, download with yt-dlp, and clean up. All state lives in SQLite plus a `/data` directory.

**Tech Stack:** Python 3.12, FastAPI + Starlette (≥0.39, for Range support in `FileResponse`), Jinja2, sqlite3 (stdlib), httpx, yt-dlp, ffmpeg, pytest. No front-end build. Plain CSS and vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-25-simplevideofeed-design.md`

## Global Constraints

- Single user, no auth, no HTTPS. Listens on port `8080`. All data under `/data` (`DATA_DIR` env, default `/data`).
- Env defaults: `POLL_MINUTES=30`, `MAX_HEIGHT=720`, `RETENTION_DAYS=14`, `WATCHED_GRACE_HOURS=24`, `MAX_CONCURRENT_DOWNLOADS=1`, `YTDLP_COOKIES_FILE` unset.
- Feed windows are rolling: Today = published within 24 h, This Week = within 7 days. Nothing older than 7 days is ever shown or downloaded.
- Watched = progress ≥ 90 % or manual ✓. Watched videos never appear in the feed or the queue.
- The UI never loads anything from YouTube: thumbnails are cached locally, there's no embed fallback, and there's no infinite scroll or "load more".
- Downloads are H.264 + AAC MP4, ≤ `MAX_HEIGHT`, falling back to the best available ≤ `MAX_HEIGHT` remuxed to MP4.
- Tokyo Night colors only, defined once as CSS variables: bg `#1a1b26`, surface `#24283b`, fg `#c0caf5`, muted `#565f89`, blue `#7aa2f7`, purple `#bb9af7`, cyan `#7dcfff`, green `#9ece6a`, red `#f7768e`, orange `#ff9e64`.
- Target browsers: modern phone and laptop browsers including iOS Safari. Smart-TV browsers are not a target.
- All times are stored as integer Unix seconds (UTC). Every function that depends on time takes `now: int` so tests can fix it.

## Review Focus

1. **A pasted URL that isn't a resolvable channel** (typo, playlist, private): the Channels page shows a readable error and saves nothing. Tested in Task 8.
2. **The same channel added twice, or the same video seen in several polls:** there are no duplicates, and the second add says "already added". Tested in Tasks 2 and 3.
3. **YouTube unreachable or a bot check during a poll or download:** that channel or video records the error, the other channels keep working, and the app stays up. Tested in Task 6.
4. **The container restarts mid-download:** a video left `downloading` goes back to `pending` at startup, and stray `.part` files are removed. Tested in Task 6.
5. **The cleanup job deletes a file that's still queued or open:** expired videos leave the queue, and `/watch/{id}` shows "no longer available" instead of a broken player. Tested in Tasks 6 and 8.

---

## File Structure

```
app/
  config.py      Settings dataclass from env
  db.py          connect(), init_db() — schema
  repo.py        all SQL: groups, channels, videos, feed, queue
  feeds.py       RSS URL building + parsing (pure)
  youtube.py     yt-dlp + httpx wrapper (only module touching YouTube)
  jobs.py        poll_channel, poll_all, download_one, cleanup, recover
  main.py        FastAPI app, routes, background loops (lifespan)
  templates/     base.html, feed.html, watch.html, queue.html, channels.html
  static/        style.css, app.js, manifest.webmanifest, icon.svg
tests/           test_<module>.py, conftest.py (tmp DB, fixed now)
Dockerfile, docker-compose.yml, .github/workflows/docker.yml, README.md,
requirements.txt, requirements-dev.txt, pyproject.toml (pytest config only)
```

## Shared Types (defined in the task named, used everywhere)

```python
# app/feeds.py (Task 4)
@dataclass
class FeedEntry: video_id: str; title: str; published_at: int; thumbnail_url: str; url: str
# app/youtube.py (Task 5)
@dataclass
class ChannelInfo: channel_id: str; title: str; handle: str | None; url: str
@dataclass
class VideoMeta: duration_s: int | None; live_status: str | None  # yt-dlp's live_status
class YouTubeError(Exception): ...
```

Video `status` values: `pending | downloading | ready | failed | waiting | skipped | expired`. `waiting` means live or upcoming, rechecked on every poll.

---

### Task 1: Scaffold, config, schema

**Files:** Create `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `.gitignore`, `app/__init__.py`, `app/config.py`, `app/db.py`, `tests/conftest.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `Settings` dataclass with fields `data_dir: Path, poll_minutes: int, max_height: int, retention_days: int, watched_grace_hours: int, max_concurrent_downloads: int, cookies_file: str | None`. Properties `db_path` (`data_dir/"feed.db"`), `videos_dir` (`data_dir/"videos"`), `thumbs_dir` (`data_dir/"thumbs"`). Also `load_settings(env: Mapping[str,str] = os.environ) -> Settings`.
- Produces: `connect(path: Path) -> sqlite3.Connection` (row_factory=Row, foreign_keys ON, WAL) and `init_db(conn) -> None` (idempotent).
- Produces fixtures `conn` (initialized in-memory DB) and `settings` (tmp_path data_dir with dirs created).

- [ ] **Step 1: Write failing tests.** `test_load_settings_defaults`: an empty env gives `poll_minutes == 30, max_height == 720, retention_days == 14, watched_grace_hours == 24, max_concurrent_downloads == 1, cookies_file is None, data_dir == Path("/data")`. `test_load_settings_overrides`: `{"MAX_HEIGHT": "480", "DATA_DIR": "/x"}` is respected. `test_init_db_creates_tables_idempotent`: calling `init_db` twice gives tables `groups, channels, videos, queue`.
- [ ] **Step 2: Run `pytest tests/test_db.py -v`.** Expected: FAIL (ImportError).
- [ ] **Step 3: Implement.** The schema follows the spec's Data section. Add `UNIQUE` on `channels.youtube_channel_id` and `videos.youtube_video_id`. `videos.channel_id` uses `ON DELETE CASCADE`, and so does `queue.video_id`. `channels.group_id` uses `ON DELETE SET NULL`. Add `videos.attempts INTEGER DEFAULT 0` and `videos.thumbnail_url TEXT`.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: scaffold, settings and schema`.

### Task 2: Repo — groups and channels

**Files:** Create `app/repo.py`, `tests/test_repo_channels.py`

**Interfaces:**
- Consumes: `conn` from Task 1.
- Produces: `create_group(conn, name) -> int`, `rename_group(conn, group_id, name)`, `delete_group(conn, group_id)`, `list_groups(conn) -> list[Row]` (ordered by position, then name). Also `add_channel(conn, info: ChannelInfo, group_id: int|None, now: int) -> int`, which raises `AlreadyAdded` if the channel ID exists. Also `list_channels(conn) -> list[Row]` (with `group_name`), `get_channel(conn, channel_id) -> Row|None`, `set_channel_group(conn, channel_id, group_id|None)`, `set_include_shorts(conn, channel_id, bool)`, `record_channel_check(conn, channel_id, now, error: str|None)`, and `delete_channel(conn, channel_id) -> list[str]`, which returns the file paths of the deleted videos so the caller can unlink them.

- [ ] **Step 1: Write failing tests.**
  - `test_add_channel_twice_raises_already_added`
  - `test_delete_group_ungroups_channels`: the channel's `group_id` becomes None.
  - `test_delete_channel_cascades_videos_and_returns_paths`: insert one video row with `file_path="/d/v/a.mp4"` directly in SQL. Delete returns `["/d/v/a.mp4"]` and the videos table is empty.
  - `test_record_channel_check_sets_error_and_clears_it`
- [ ] **Step 2: Run tests.** Expected: FAIL.
- [ ] **Step 3: Implement** in `app/repo.py`. `ChannelInfo` is imported from `app.youtube`. Until Task 5 exists, define the dataclass there first (just the dataclasses and `YouTubeError`).
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: groups and channels repo`.

### Task 3: Repo — videos, feed, watched, queue

**Files:** Modify `app/repo.py`. Create `tests/test_repo_feed.py`, `tests/test_repo_queue.py`

**Interfaces:**
- Produces:
  - `upsert_video(conn, channel_id, entry: FeedEntry, status: str, now) -> bool`: True if newly inserted. An existing row keeps its status, watched_at and file.
  - `get_video(conn, video_id) -> Row|None`, `set_video_meta(conn, video_id, duration_s, status)`, `set_status(conn, video_id, status, error=None, file_path=None)`, `set_thumbnail(conn, video_id, path)`.
  - `next_pending(conn) -> Row|None`: the oldest `published_at` with status `pending` and `attempts < 3`.
  - `bump_attempts(conn, video_id) -> int`.
  - `feed(conn, window: Literal["today","week"], group_id: int|None, now) -> list[Row]`: rows include `channel_title`, `in_queue`.
  - `feed_counts(conn, group_id, now) -> dict[str,int]` with keys `today` and `week`.
  - `mark_watched(conn, video_id, now)`: also removes the video from the queue. `unmark_watched(conn, video_id)`.
  - `save_progress(conn, video_id, position_s: float, duration_s: float, now) -> bool`: returns True when this call marked the video watched (ratio ≥ 0.9).
  - `queue_add(conn, video_ids: list[int])`: appends, skipping ones already queued or watched. `queue_remove(conn, video_id)`, `queue_move(conn, video_id, direction: Literal["up","down"])`, `queue_list(conn) -> list[Row]` (ordered, with title, channel and status).
- Feed visibility rule: status in `('pending','downloading','ready','failed')`, `watched_at IS NULL`, `published_at >= now - window_seconds` (86400 or 604800), and channel in the group when `group_id` is given. Newest first.

- [ ] **Step 1: Write failing tests** with a fixed `now = 1_800_000_000`.
  - `test_today_vs_week_windows`: videos published 2 h, 30 h and 8 days ago. Today shows [2 h]. Week shows [2 h, 30 h], newest first.
  - `test_watched_and_skipped_hidden`
  - `test_group_filter`
  - `test_feed_counts`
  - `test_upsert_same_video_twice_is_noop`: second call returns False, status unchanged.
  - `test_save_progress_marks_watched_at_90_percent`: 89/100 returns False and stores the position. 90/100 returns True and sets `watched_at`.
  - `test_mark_watched_removes_from_queue`
  - `test_unmark_watched_restores_to_feed`
  - `test_queue_add_skips_duplicates_and_watched`
  - `test_queue_move_up_down_and_bounds`: moving the first item up is a no-op.
  - `test_next_pending_respects_attempts`
- [ ] **Step 2: Run `pytest tests/test_repo_feed.py tests/test_repo_queue.py -v`.** Expected: FAIL.
- [ ] **Step 3: Implement.** Queue positions are integers. Append uses `max+1`, and move swaps with the neighbour.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: videos, feed and queue repo`.

### Task 4: RSS feeds

**Files:** Create `app/feeds.py`, `tests/test_feeds.py`, `tests/fixtures/feed.xml` (a hand-written Atom sample with 2 entries in YouTube's format: `yt:videoId`, `published`, `media:group/media:thumbnail@url`, `link@href`)

**Interfaces:**
- Produces:
  - `feed_url(channel_id: str, long_form_only: bool) -> str`. With `long_form_only` it returns `https://www.youtube.com/feeds/videos.xml?playlist_id=UULF` + `channel_id[2:]`. Otherwise it returns `https://www.youtube.com/feeds/videos.xml?channel_id=` + `channel_id`.
  - `parse_feed(xml: str) -> list[FeedEntry]`, parsed with `xml.etree.ElementTree` and namespaces. `published_at` is converted to Unix seconds.
  - `is_short_url(url: str) -> bool`: True if `/shorts/` is in the URL.

- [ ] **Step 1: Write failing tests.** `test_feed_url_long_form` (`"UCabc"` → `...playlist_id=UULFabc`), `test_feed_url_all`, `test_parse_feed_fixture` (2 entries with exact id, title, timestamp, thumbnail), `test_parse_feed_empty_feed_returns_empty_list`, `test_is_short_url`.
- [ ] **Step 2: Run tests.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: RSS feed parsing`.

### Task 5: YouTube wrapper

**Files:** Modify `app/youtube.py`. Create `tests/test_youtube.py`

**Interfaces:**
- Produces:
  - `resolve_channel(url: str, cookies_file: str|None) -> ChannelInfo`: yt-dlp `extract_info(url, download=False, process=False)` with `extract_flat=True`. It reads `channel_id` (or `uploader_id`/`id` when it starts with `UC`). It raises `YouTubeError("Couldn't find a channel at that link")` on any yt-dlp error or when no `UC…` ID is found.
  - `fetch_feed(url: str) -> str`: httpx GET with a 15 s timeout. It raises `YouTubeError` on non-200, and on 404 its message contains `"404"`.
  - `fetch_meta(video_id: str, cookies_file) -> VideoMeta`
  - `download_thumbnail(url: str, dest: Path) -> None`
  - `is_short(video_id: str) -> bool`: HEAD `https://www.youtube.com/shorts/{id}` without following redirects. 200 means it's a Short. Used only when the UULF feed is unavailable.
  - `ydl_download_opts(video_id, videos_dir: Path, max_height: int, cookies_file) -> dict`
  - `download_video(video_id, videos_dir, max_height, cookies_file) -> Path`: returns `videos_dir/f"{video_id}.mp4"`, and raises `YouTubeError` on failure after deleting `{video_id}*.part`.
- Exact format string in `ydl_download_opts`: `f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/b[height<={h}][ext=mp4]/bv*[height<={h}]+ba/b[height<={h}]"`. Also `merge_output_format="mp4"`, `outtmpl=str(videos_dir/"%(id)s.%(ext)s")`, `quiet=True`, `noprogress=True`, and `cookiefile` only when set.

- [ ] **Step 1: Write failing tests.** Monkeypatch `yt_dlp.YoutubeDL` and use `httpx.MockTransport`. Tests:
  - `test_ydl_download_opts_720_h264_mp4`: exact format string, `merge_output_format == "mp4"`, no `cookiefile` key by default.
  - `test_ydl_download_opts_includes_cookies_when_set`
  - `test_resolve_channel_reads_channel_id`
  - `test_resolve_channel_error_becomes_youtube_error`
  - `test_fetch_feed_non_200_raises`
  - `test_download_video_failure_removes_part_files`
- [ ] **Step 2: Run tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep a module-level `httpx.Client` factory that tests can override.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: yt-dlp wrapper`.

### Task 6: Jobs — poll, download, cleanup, recover

**Files:** Create `app/jobs.py`, `tests/test_jobs.py`

**Interfaces:**
- Consumes: Tasks 2 to 5. `youtube` is passed in as a module-like object so tests can supply a fake.
- Produces:
  - `poll_channel(conn, settings, channel: Row, yt, now) -> int`: returns the number of new videos.
  - `poll_all(conn, settings, yt, now) -> None`
  - `download_one(conn, settings, yt) -> bool`: False when nothing is pending.
  - `cleanup(conn, settings, now) -> int`: returns the number of files deleted.
  - `recover(conn, settings) -> None`
- `poll_channel` rules:
  1. Fetch `feed_url(id, long_form_only=not include_shorts)`. On a `YouTubeError` containing `"404"` with `long_form_only`, fall back to the full feed and set `fallback_shorts=True`.
  2. Ignore entries older than `now - 7*86400`.
  3. For new entries (`upsert_video` returned True), call `fetch_meta`. A `live_status` of `is_live` or `is_upcoming` gives status `waiting`. `was_live` or `post_live` gives `skipped`. If `fallback_shorts` is set and (`is_short_url(entry.url)` or `yt.is_short(id)`), the status is `skipped`. Otherwise it's `pending`. Store the duration.
  4. Re-check `waiting` rows for this channel on every poll using the same rules.
  5. Download each new thumbnail to `thumbs_dir/{id}.jpg`. A thumbnail failure is ignored.
  6. Call `record_channel_check(..., error=None)`. On any `YouTubeError`, record `str(e)` and return 0 without raising.
- `poll_all` catches exceptions per channel, so one bad channel never stops the others.
- `download_one`: takes `next_pending` and sets `downloading`. Success sets `ready` with `file_path`. On a `YouTubeError` it calls `bump_attempts`: under 3 attempts it goes back to `pending` with the error, and at 3 it becomes `failed`.
- `cleanup`:
  - Watched with a file and `watched_at <= now - grace*3600`: delete the file and clear `file_path`, keeping status `ready` so the video stays hidden because it's watched.
  - Any video with `published_at <= now - retention_days*86400`: delete the file and thumbnail, set `expired`, and remove it from the queue.
  - Missing files are tolerated.
- `recover`: sets every `downloading` back to `pending` and deletes `videos_dir/*.part`.

- [ ] **Step 1: Write failing tests** with a `FakeYT` class whose behaviour is set per test.
  - `test_poll_inserts_new_pending_and_skips_old`
  - `test_poll_uses_long_form_feed_when_shorts_excluded`: asserts the requested URL contains `UULF`.
  - `test_poll_falls_back_and_skips_shorts_on_404`
  - `test_poll_live_waiting_then_skipped_when_was_live`
  - `test_poll_error_recorded_other_channels_continue`: two channels, the first raises. The second still gets videos, and the first has `last_error` set.
  - `test_download_success_sets_ready`
  - `test_download_retries_then_failed`: three failures end in `failed` with an error.
  - `test_cleanup_watched_after_grace`: 23 h after watching the file is kept. 25 h after, it's deleted.
  - `test_cleanup_expires_after_retention_and_removes_from_queue`
  - `test_recover_resets_downloading_and_removes_part_files`
- [ ] **Step 2: Run `pytest tests/test_jobs.py -v`.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: poll, download and cleanup jobs`.

### Task 7: App, API, background loops

**Files:** Create `app/main.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `create_app(settings: Settings, yt=youtube, start_background: bool = True) -> FastAPI`. The module-level `app = create_app(load_settings())` is only created when not under test. Use a `get_app()` factory referenced by uvicorn as `app.main:get_app --factory`.
- Lifespan: `init_db`, `recover`, then (if `start_background`) three asyncio tasks that run the blocking jobs with `asyncio.to_thread`:
  - poll every `poll_minutes`, with the first run immediately.
  - download loop: call `download_one` and sleep 30 s when it returns False. Run `max_concurrent_downloads` such loops, each with its own connection.
  - cleanup hourly.
- JSON API (all return JSON; errors use `{"error": msg}` with a 4xx status):
  - `POST /api/videos/{id}/watched`, then `DELETE /api/videos/{id}/watched` to undo.
  - `POST /api/videos/{id}/progress` with body `{"position": float, "duration": float}`, returning `{"watched": bool}`.
  - `POST /api/videos/{id}/retry`: `failed` goes to `pending` with attempts reset to 0.
  - `GET /api/queue`: a list of `{id, title, channel, status, src}`.
  - `POST /api/queue` with `{"video_ids": [int]}`. `POST /api/queue/{id}/move` with `{"direction": "up"|"down"}`. `DELETE /api/queue/{id}`.
  - Media: `GET /media/{video_id}.mp4` and `GET /thumbs/{video_id}.jpg` use `FileResponse`, giving Range support. They return 404 when the file is missing.
- Forms (HTML posts that redirect back with 303): `POST /channels`, which takes `url` and `group_id` and then triggers `poll_channel` in the background. Also `POST /channels/{id}/group`, `POST /channels/{id}/shorts`, `POST /channels/{id}/delete` (unlinks the returned files), `POST /groups`, `POST /groups/{id}/rename` and `POST /groups/{id}/delete`. Errors such as `AlreadyAdded` or `YouTubeError` redirect with `?error=<message>`.

- [ ] **Step 1: Write failing tests** with TestClient, `start_background=False` and a `FakeYT`.
  - `test_progress_marks_watched_at_90`
  - `test_watched_then_undo`
  - `test_queue_add_list_move_delete`
  - `test_media_supports_range`: write a 1000-byte file. A GET with `Range: bytes=0-99` gives 206 and a 100-byte body.
  - `test_media_missing_404`
  - `test_add_channel_bad_url_redirects_with_error_and_saves_nothing`
  - `test_add_channel_twice_shows_already_added`
  - `test_delete_channel_unlinks_files`
  - `test_retry_failed_resets_to_pending`
- [ ] **Step 2: Run `pytest tests/test_api.py -v`.** Expected: FAIL.
- [ ] **Step 3: Implement.** Open one SQLite connection per request with `check_same_thread=False` in the dependency.
- [ ] **Step 4: Run the full suite with `pytest -q`.** Expected: all PASS.
- [ ] **Step 5: Commit** with `feat: FastAPI app, API and background loops`.

### Task 8: Pages, theme and player JS

**Files:** Create `app/templates/{base,feed,watch,queue,channels}.html`, `app/static/{style.css,app.js,manifest.webmanifest,icon.svg}`, `tests/test_pages.py`. Modify `app/main.py` to add page routes and mount `/static`.

**Interfaces:**
- Pages:
  - `GET /?tab=today|week&group=<id>`: defaults to `today`.
  - `GET /watch/{id}`
  - `GET /queue`
  - `GET /channels`
- Fixed copy:
  - Tab labels: `Today (N)` and `This Week (N)`.
  - Empty feed: `You're all caught up.`
  - Queue finished: `Queue finished.`
  - Not-yet-downloaded button: `downloading…`
  - Failed button: `failed — retry`
  - Expired or missing video page: `This video is no longer available.`
- Feed item: local thumbnail (`/thumbs/{yid}.jpg`, falling back to a muted placeholder box), title, channel, duration `m:ss`/`h:mm:ss`, relative age (`3h ago`, `2d ago`). Buttons: `▶ Play` (only when `ready`), `+ Queue` (`Queued` when already queued), `✓`.
- The `Add all to queue` button posts the visible IDs.
- `app.js`:
  - ✓ fades the item out and shows a 5 s `Undo` toast that calls DELETE.
  - The `<video>` element posts progress every 10 s, on `pause` and on `ended` (`ended` sends position = duration).
  - On load, playback seeks to `data-position`.
  - The queue page plays item 0. On `ended` it marks the video watched (via progress), fetches `/api/queue` and loads the next item, which needs no user gesture because the user started playback. When the queue is empty it shows `Queue finished.`
- `style.css`: Tokyo Night variables from Global Constraints, a mobile-first single column, max width 900 px, tap targets ≥ 44 px, and `:focus-visible` outlines in blue.
- `base.html` links the manifest, sets `<meta name="theme-color" content="#1a1b26">` and has the nav `Feed · Queue (N) · Channels`.

- [ ] **Step 1: Write failing tests.**
  - `test_feed_empty_shows_caught_up`
  - `test_feed_tabs_show_counts`
  - `test_feed_never_references_youtube_hosts`: rendered HTML with videos contains no `ytimg.com` and no `youtube.com/embed`.
  - `test_feed_group_chip_filters`
  - `test_not_ready_item_has_no_play_link`
  - `test_watch_expired_shows_unavailable`
  - `test_channels_page_shows_error_param`
  - `test_queue_page_renders_items`
- [ ] **Step 2: Run `pytest tests/test_pages.py -v`.** Expected: FAIL.
- [ ] **Step 3: Implement** the templates, CSS and JS.
- [ ] **Step 4: Run `pytest -q`.** Expected: all PASS.
- [ ] **Step 5: Manual check.** Run `DATA_DIR=$(mktemp -d) uvicorn app.main:get_app --factory --port 8080` with seeded fake data (a `scripts/seed_demo.py` is allowed). Take Playwright screenshots of `/`, `/queue` and `/channels` at a 390×844 viewport and at 1280×800. Expected: dark Tokyo Night look, no horizontal scroll at 390 px.
- [ ] **Step 6: Commit** with `feat: pages, Tokyo Night theme, player JS`.

### Task 9: Docker, CI, README, smoke test

**Files:** Create `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.github/workflows/docker.yml`, `README.md`

**Interfaces:**
- Dockerfile:
  - Base `python:3.12-slim`, with `apt-get install -y --no-install-recommends ffmpeg`.
  - `pip install -r requirements.txt` with yt-dlp unpinned (`yt-dlp>=2025.1.1`) so every build gets the latest.
  - `EXPOSE 8080`, `VOLUME /data`.
  - `CMD ["uvicorn","app.main:get_app","--factory","--host","0.0.0.0","--port","8080"]`
  - `HEALTHCHECK` curls `/healthz`. Add a `GET /healthz` route returning `{"ok": true}`, with a test in `tests/test_api.py`.
- `docker-compose.yml`:
  - `image: ghcr.io/elaktrato/simplevideofeed:latest`
  - `ports: ["8080:8080"]`
  - `volumes: ["./data:/data"]`
  - Every env var with its default, plus a commented `YTDLP_COOKIES_FILE`.
  - `restart: unless-stopped`
- Workflow:
  - Triggers: `push` to `main`, tags `v*`, `schedule: cron "17 4 * * 1"` (weekly) and `workflow_dispatch`.
  - Job 1 runs pytest. Job 2 uses `docker/setup-qemu-action`, `setup-buildx-action`, `login-action` (GHCR with `GITHUB_TOKEN`, `packages: write`) and `build-push-action`.
  - Platforms `linux/amd64,linux/arm64`, tags `latest` plus the git tag.
- README:
  - What it is.
  - Portainer stack setup: paste the compose file, and on first push make the package public in GitHub Packages.
  - Open it at `http://<server-tailscale-name>:8080`.
  - It's reachable on the LAN too, and binding to `100.x.y.z:8080:8080` makes it Tailscale-only.
  - Env var table.
  - Updating yt-dlp: re-pull the image.
  - Cookies tip for bot checks.

- [ ] **Step 1: Write the failing test** `test_healthz`, then implement the route and run `pytest -q`. Expected: all PASS.
- [ ] **Step 2: Build** with `docker build -t svf .` if Docker is available in the build environment. Expected: success. If Docker isn't available, record that and rely on CI.
- [ ] **Step 3: Live smoke test.** Run the app locally and add one real small channel through `/channels`. Expected: the channel resolves, entries appear and one video reaches `ready` and plays. If YouTube is unreachable or bot-checked from the build environment, record exactly what failed. The owner's first run is then the smoke test.
- [ ] **Step 4: Commit and push** with `feat: Docker image, CI and README`. Check that the GitHub Actions run passes with `gh run list` if `gh` works, otherwise ask the owner to look.

---

## Self-review notes

- **Spec coverage:** every spec item has a task:
  - Adding channels, groups and the Shorts toggle: Tasks 2, 7 and 8.
  - Today and Week tabs, counts and chips: Tasks 3 and 8.
  - Watched at 90 %, ✓ and undo: Tasks 3, 7 and 8.
  - Queue, add all and reorder: Tasks 3, 7 and 8.
  - Resume position: Tasks 3 and 8.
  - Long-form feed and fallback: Tasks 4 and 6.
  - Live handling, 7-day backfill, local thumbnails, retries and the retry button: Tasks 6, 7 and 8.
  - Cookies: Task 5.
  - Cleanup grace and retention: Task 6.
  - Range: Task 7.
  - Tokyo Night and manifest: Task 8.
  - Docker, multi-arch, weekly rebuild and README: Task 9.
- **Small spec deviation:** a watched video's file is deleted after the grace period, but its row stays so it remains hidden. Expiry removes the thumbnail too.
