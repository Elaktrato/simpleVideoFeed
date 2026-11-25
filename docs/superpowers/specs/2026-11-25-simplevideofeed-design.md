# simpleVideoFeed — Design

*Repo: github.com/Elaktrato/simpleVideoFeed. Date: 2026-11-25.*

## Intent

The owner has a YouTube habit driven by endless scroll and recommendations. They want a
small, private, self-hosted app that shows **only** new uploads from channels they chose
themselves, and **ends**: once they've watched or dismissed everything, the feed is empty
and they're done. Videos are downloaded to the home server and played from there, so
watching never touches youtube.com (and its recommendations, comments, autoplay).

Success: opening the app shows a short, finite list; working through it ends at
"You're all caught up"; the owner stops opening YouTube itself.

## Goals

- Add channels by pasting any channel URL (`youtube.com/@handle`, `/channel/UC…`, `/c/…`,
  or a video URL from the channel). Optionally assign each channel to one group
  (category). Remove channels. Rename/create/delete groups.
- Two feed tabs:
  - **Today**: uploads published in the last 24 hours (rolling window).
  - **This Week**: uploads published in the last 7 days (rolling; includes Today's).
  - Newest first. Each item: thumbnail, title, channel, duration, relative age.
  - Group filter chips (All + each group). Ungrouped channels appear under All only.
  - Tab labels show unwatched counts.
- **Watched videos disappear from both tabs.** Empty state: "You're all caught up."
- A video counts as watched when played to ≥90%, or when the user taps ✓ (dismiss).
- **Queue**: a "+ Queue" button per video, plus (my call) an "Add all to queue" button
  that adds every video currently shown (respecting tab + group filter). A Queue view
  plays items in order and auto-advances; each finished item is marked watched and leaves
  the queue. Items can be removed/reordered (reorder: simple up/down is fine).
- Videos auto-download to the server; playback is from the local file in an HTML5
  `<video>` element, served by the app.
- Works well in modern phone and laptop browsers (incl. iOS Safari). Responsive, no
  install. Native smart-TV browsers are **not** a target for now (owner's decision).
  (My call: include a basic web app manifest so it can be added to a phone home screen.)
- Watched state, queue, and playback position live on the **server** (single user), so a
  queue started on the phone can continue on the laptop.

## Non-goals

- No login/auth, no multi-user. Access is via the owner's Tailscale network only.
- No HTTPS inside the app (Tailscale handles transport).
- No smart-TV / remote-control (D-pad) navigation in v1.
- No YouTube search, recommendations, comments, likes, or "related videos".
- No embedded YouTube player fallback, ever. If a file isn't downloaded yet, the item
  shows "downloading…" (or "failed — retry") and is not playable.
- No permanent archive. No video older than 7 days is ever shown.
- No import of YouTube subscriptions in v1 (possible later).

## Anti-goals (would be a failure even if it works)

- Anything infinite: infinite scroll, "load more", older-than-a-week content.
- Autoplay outside the explicit queue.
- Anything that sends the user to youtube.com as part of normal use. (A small "source"
  link on the player page is acceptable but not prominent — builder's call; leaving it
  out is fine.)

## Constraints

- Runs as a Docker container on the owner's home server, managed via Portainer
  (stack / docker-compose). Must work as a Portainer stack from a published image.
- Public GitHub repo; image built by GitHub Actions and published to GHCR.
  Build multi-arch (amd64 + arm64) since the home server hardware is unknown.
- Reached over Tailscale from anywhere, so video bitrate matters on mobile data.
- YouTube changes often; `yt-dlp` must be easy to keep current (see Operations).

## Approaches considered

- **A. Python (FastAPI) + SQLite + yt-dlp as a library, server-rendered HTML with a
  little vanilla JS.** One container, one data volume, no front-end build. — **Chosen.**
  Least code that delivers exactly this feature set; yt-dlp is native Python.
- **B. Node backend + React SPA.** Slicker UI, but twice the moving parts and a build
  pipeline, and yt-dlp would be shelled out to. Rejected as over-built.
- **C. Pinchflat + Jellyfin.** Almost no code, but no Today/Week tabs, no "caught up"
  ending, no quick queue; Jellyfin's library browsing recreates the browsing habit.
  Rejected as missing the point.

## Design (decided)

### Stack
- Python 3.12, FastAPI, Jinja2 templates, SQLite (single file), `yt-dlp` (Python API),
  `ffmpeg` in the image (for merging video+audio), a background scheduler running
  in-process (e.g. APScheduler or a simple asyncio loop — builder's call).
- Single container, single process. Port 8080.

### Data (SQLite)
- `groups(id, name, position)`
- `channels(id, youtube_channel_id, title, handle, url, group_id NULL, include_shorts
  bool default false, added_at, last_checked_at, last_error)`
- `videos(id, youtube_video_id UNIQUE, channel_id, title, published_at, duration_s,
  thumbnail_path, file_path NULL, status [pending|downloading|ready|failed|skipped|
  expired], error, watched_at NULL, position_s default 0, is_short, is_live)`
- `queue(video_id, position)`
Schema details are the builder's; the above is the required information.

### Fetching new videos
- Resolve a pasted URL to a channel ID once at add time (yt-dlp can extract
  `channel_id` from any channel or video URL). Store title/handle.
- Poll each channel's RSS feed every **30 min** (configurable):
  `https://www.youtube.com/feeds/videos.xml?channel_id=UC…`. Free, no API key.
  - Builder note: the `playlist_id=UULF<id without "UC">` variant reportedly returns
    long-form uploads only (excludes Shorts and lives). Verify; if it works, prefer it
    when `include_shorts` is false. Otherwise detect Shorts via yt-dlp metadata
    (e.g. URL contains `/shorts/`, or duration ≤ 60s with vertical aspect) and mark
    `skipped`.
- Only consider videos published within the last 7 days. When a channel is first added,
  backfill its last 7 days.
- **Livestreams and premieres:** skip while `is_live`/upcoming. If a completed stream's
  VOD later becomes available within the window, it may be downloaded — builder's call;
  simplest acceptable behavior is skipping live content entirely.
- Duration and a local copy of the thumbnail come from yt-dlp metadata / the feed;
  thumbnails are cached locally so the UI never hot-links YouTube.

### Downloading
- Background worker, **one download at a time** (configurable), oldest-pending first.
- Format: best video ≤ **720p** (configurable `MAX_HEIGHT`) in **H.264 + AAC, MP4
  container**, so it plays in every browser incl. iOS Safari. Fall back
  to the best available ≤ MAX_HEIGHT and remux to MP4 if H.264 isn't offered.
- Failures: record error, retry with backoff (e.g. 3 attempts), then `failed` with a
  manual "retry" button in the UI.
- Optional cookies file (mounted path via env var) passed to yt-dlp, since YouTube
  sometimes demands sign-in/bot checks. Off by default; document it in the README.

### Storage & cleanup (my calls)
- Files: `/data/videos/<youtube_video_id>.mp4`, thumbnails in `/data/thumbs/`, DB at
  `/data/feed.db`.
- Cleanup job hourly:
  - Delete files of **watched** videos 24 h after `watched_at` (grace period for an
    accidental ✓; an "undo" toast right after ✓ is nice-to-have).
  - Delete files of **any** video older than **14 days** after `published_at`
    (configurable `RETENTION_DAYS`), mark `expired`.
- Because the feed shows at most 7 days, unwatched videos older than 7 days silently
  fall out of the feed (they're not "debt" carried forward).

### Watched & playback
- Player page (or modal) with HTML5 `<video>`, file served with HTTP Range support
  (required for seeking and iOS).
- Player reports position to the server periodically (e.g. every 10 s and on pause);
  reopening a video resumes from `position_s` (on any device).
- At ≥90% played → mark watched. ✓ button marks watched without playing.
- Queue view: plays queue in order; on `ended` (or ≥90% then ended) mark watched, remove
  from queue, load next. When queue empties: "Queue finished."

### Pages
- `/` Feed: tabs Today / This Week, group chips, list, "Add all to queue".
- `/queue` Queue player + list.
- `/watch/<id>` single video player.
- `/channels` Add channel (URL + group dropdown), list channels by group with
  last-checked time / error, change group, toggle Shorts, remove. Manage groups.
- Removing a channel removes its videos and files.
- Plain, calm styling using the **Tokyo Night** color palette (owner's choice): dark
  "Night" variant always (bg `#1a1b26`, surface `#24283b`, fg `#c0caf5`, muted
  `#565f89`, accent blue `#7aa2f7`, purple `#bb9af7`, cyan `#7dcfff`, green `#9ece6a`,
  red `#f7768e`, orange `#ff9e64`). Colors defined once as CSS variables. Large tap
  targets for phone.

### Configuration (env vars)
`POLL_MINUTES=30`, `MAX_HEIGHT=720`, `RETENTION_DAYS=14`, `WATCHED_GRACE_HOURS=24`,
`MAX_CONCURRENT_DOWNLOADS=1`, `TZ` (for display only; windows are rolling),
`YTDLP_COOKIES_FILE` (optional). Volume: `/data`.

### Deployment
- `Dockerfile` (python slim + ffmpeg), `docker-compose.yml` example for a Portainer
  stack (image from GHCR, port 8080, `/data` volume, env vars, `restart: unless-stopped`).
- GitHub Actions: build & push multi-arch image on push to main and on tags, plus a
  **weekly scheduled rebuild** so the image picks up the latest yt-dlp. README explains
  "re-pull the image in Portainer to update yt-dlp."
- **Network exposure (owner confirmed):** the default compose uses a
  plain `8080:8080` port mapping, so the app is reachable on the home LAN *and* via
  Tailscale (the host's Tailscale address). Anyone on the home network can open it; this
  is accepted since there's no auth by design. The README documents an optional stricter
  variant: bind the port to the host's Tailscale IP (`100.x.y.z:8080:8080`).
- README: setup in Portainer, network/Tailscale note as above, env vars, cookies tip.

### Testing
- Unit tests (pytest) for: RSS parsing, channel-URL resolution (mocked), Today/Week
  window logic, watched/queue state transitions, cleanup rules, Shorts filtering.
- Downloads mocked in tests; one manual smoke test against a real channel (from the
  build environment if YouTube is reachable; otherwise the owner's first run).

## Left to the builder
- Exact schema, module layout, scheduler choice, JS details, visual styling within the
  "plain, calm, Tokyo Night" brief, retry/backoff numbers, whether to keep a discreet source link.
