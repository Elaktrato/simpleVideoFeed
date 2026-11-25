# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
