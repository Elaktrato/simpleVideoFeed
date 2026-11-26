# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0] - 2026-11-26

### Added
- **Unfollow** a channel straight from the full-screen player or a video's ⋯ menu (tap twice to confirm). The video you're watching keeps playing; the channel's other videos leave the feed and queue right away, and its files are cleaned up afterwards.
- Re-adding an unfollowed channel on the Channels page brings it back.

## [1.1.0] - 2026-11-25

### Added
- Persistent player: videos keep playing while you browse, with a player bar at the bottom.
- Full-screen player with ⏮ ⏯ ⏭, **✓ Done**, and an **Up next** list you can reorder or remove.
- **Play next** to play a video right after the current one.
- Lock-screen and headphone controls (Media Session).
- **Poster Grid** look: the newest video as a large poster, the rest in a grid. Each card has a ⋯ menu with Play, Play next, Add to queue and Mark watched.
- Undo after ✓ puts the video back in its place in the queue.
- `/watch/{id}` links open the full-screen player, paused.

### Changed
- The queue now lives in the player. The separate Queue page is gone.
- **+ Queue** adds to the end of the queue. Adding to an empty queue loads the video paused.
- **▶ Play** on another video switches immediately. A paused current video moves to Up next instead of being dropped.
- Fonts (Geist, Geist Mono, Space Grotesk) are bundled with the app. Nothing loads from third parties.

## [1.0.1] - 2026-11-25

### Changed
- Python 3.15 base image (was 3.12).
- GitHub Actions updated to their current major versions (no more Node.js 20 deprecation warnings).
- Minimum versions raised to current releases: FastAPI 0.165, Starlette 1.10, Uvicorn 0.54, yt-dlp 2026.11.23, pytest 9.2.

## [1.0.0] - 2026-11-25

### Added
- Channels you choose, optionally sorted into groups, with a per-channel toggle to include Shorts.
- **Today** (last 24 h) and **This Week** (last 7 days) feeds, filterable by group, that end at "You're all caught up."
- Automatic downloads (H.264/AAC MP4, up to 720p by default) played from your own server; nothing loads from YouTube while browsing.
- Watched at 90% or with ✓ (with Undo); playback position is remembered across devices.
- Server-side queue: **+ Queue**, **Add all to queue**, reorder, auto-advance.
- Automatic cleanup: watched videos after 24 h, everything 14 days after upload.
- Retries with backoff for failed downloads, plus a manual retry button.
- Tokyo Night theme; mobile-first layout; add-to-home-screen manifest.
- Docker image for amd64/arm64 on GHCR, rebuilt weekly for the latest yt-dlp.
