# Persistent Player & Poster Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A YouTube-style player that keeps playing across pages, a queue built around a "current" video, and the "Poster Grid" look for every page.

**Architecture:** Pages stay server-rendered (Jinja). A small navigation script swaps only `<main>` on link clicks and form submits, so a persistent shell holding the single `<video>`, the pill bar and the expanded player is never replaced. The queue stays server-side. Two new repo operations, play-now and play-next, are built around "current = first downloaded queue item".

**Tech Stack:** FastAPI, Jinja2, SQLite, vanilla JS (ES2020, no build), CSS. Self-hosted woff2 fonts: Geist, Geist Mono, Space Grotesk. Playwright (Node, pre-installed) for the browser check.

**Spec:** `docs/superpowers/specs/2026-11-25-player-redesign-design.md`. The visual reference is mockup B. Its source is copied into the repo as `docs/design/feed-styles.html` in Task 2.

## Global Constraints

- Tokyo Night palette as the existing CSS tokens (bg `#1a1b26`, bg-deep `#16161e`, surface `#24283b`, surface-2 `#292e42`, line `#2f3549`, fg `#c0caf5`, fg-dim `#a9b1d6`, muted `#565f89`, blue `#7aa2f7`, purple `#bb9af7`, cyan `#7dcfff`, green `#9ece6a`, red `#f7768e`, orange `#ff9e64`, yellow `#e0af68`). Dark only.
- Fonts: Geist (UI), Space Grotesk (headings), Geist Mono (numbers), served from `/static/fonts/`. **No request to any third-party host at runtime.** No `fonts.googleapis`, `ytimg` or `youtube.com` in any page.
- Nothing autoplays except the next queue item after one ends. Adding to an empty queue loads the video **paused**.
- The single `<video>` element is created once and never replaced or recreated.
- Works at 360px with no horizontal scroll, and in iOS Safari (`playsinline`; first play only from a tap).
- Copy that stays: `You're all caught up.`, `This video is no longer available.`, `downloading…` → now `Downloading`, `failed — retry` → now `Retry`.
- The release goes through git-flow: `feature/player-redesign` → `develop` → `release/1.1.0` → `main` and tag `v1.1.0`.

## Review Focus

1. **The queue only holds videos that are still downloading:** the bar shows item 1 with "Downloading" and a disabled ⏯. When a download finishes, the bar becomes playable on its next refresh, not stuck forever. Covered in Task 4: the player re-polls `/api/queue` every 30 s while no item is ready.
2. **A form submit on Channels while a video plays,** such as adding a channel or a bad URL: the video keeps playing and the error banner shows. Covered in Task 2 (nav handles 303 → `?error=`) and the Task 4 e2e step.
3. **Back and Forward after several in-app navigations** restore the right page without reloading the player. Covered in the Task 4 e2e step.
4. **Two devices:** a phone and a laptop both have the app open, and the queue changes on one. The other picks up the server queue on its next queue action or reload, and never posts progress for a video that's no longer current. Covered in Task 1: `save_progress` is harmless for any id.
5. **A queue item expires** (cleanup) while it's current: the player treats the 404 as unplayable and skips it (v1 logic). Covered in the Task 4 e2e step.

---

## File Structure

```
app/repo.py            + queue_current, queue_play_now, queue_play_next, queue_insert_at; save_progress no longer dequeues
app/main.py            + /api/queue/play, /api/queue/next; undo with queue_index; queue JSON fields; /queue removed; /watch renders feed
app/templates/base.html     persistent shell (main + player + toast + single <video>)
app/templates/feed.html     header, tabs, chips, poster, grid, card menu
app/templates/channels.html restyled
app/templates/_card.html    one grid card (macro), reused by poster/grid
(delete) queue.html, watch.html
app/static/css/app.css      all styles (replaces style.css)
app/static/js/nav.js        in-app navigation (swap <main>)
app/static/js/player.js     persistent player, bar, expanded view, media session
app/static/js/feed.js       card actions, ⋯ menu, toast/undo, channels-page helpers
app/static/fonts/*.woff2    Geist 400/500/600/700, Geist Mono 500, Space Grotesk 500/600/700 (latin)
scripts/e2e_player.mjs      Playwright check against a seeded server (manual, not CI)
docs/design/feed-styles.html  mockup B source (reference)
```

---

### Task 1: Queue model around "current"

**Files:** Modify `app/repo.py`, `app/main.py`. Test: `tests/test_repo_queue.py`, `tests/test_api.py`

**Interfaces:**
- `repo.queue_current(conn) -> Row | None`: the first queue row (by position) whose `file_path IS NOT NULL` and `status='ready'`.
- `repo.queue_play_now(conn, video_id: int, keep_current: bool) -> None`: puts `video_id` first. The old current, if it exists and is a different video, either moves right behind the new video (`keep_current=True`) or leaves the queue (`False`). Neither case touches `watched_at` or `position_s`.
- `repo.queue_play_next(conn, video_id: int) -> None`: places `video_id` right after `queue_current`. With no current, it goes first. If the video is already queued, it moves instead of duplicating. Watched, expired and skipped videos are ignored, as in `queue_add`.
- `repo.queue_insert_at(conn, video_id: int, index: int) -> None`: inserts at the 0-based index, clamped to the queue length.
- Shared helper `_queue_write(conn, ids: list[int])` renumbers positions 1..n.
- `save_progress` sets `watched_at` at ≥ 0.9 **without** removing the video from the queue. `mark_watched` still removes it, which is the ✓ / Done / ended path.
- API:
  - `POST /api/queue/play {video_id, keep_current}` and `POST /api/queue/next {video_id}` both return the queue JSON.
  - `DELETE /api/videos/{id}/watched` accepts an optional JSON body `{queue_index: int}`. When present, the video is unmarked and then re-queued at that index.
  - The queue JSON items gain `thumb` (`/thumbs/{yid}.jpg` or null), `duration` (int or null) and `ready` (bool), on top of the existing `id, title, channel, status, position, src`.

- [ ] **Step 1: Write failing tests** in `tests/test_repo_queue.py`, using the existing helpers `make_channel` and `make_video(status="ready")`:
  - `test_queue_current_skips_not_downloaded`: queue [pending a, ready b] → current is b.
  - `test_play_now_drops_playing_current`: queue [a,b], play_now(c, keep_current=False) → [c,b]. a has `watched_at` None and keeps its position.
  - `test_play_now_keeps_paused_current_as_next`: queue [a,b], play_now(c, True) → [c,a,b].
  - `test_play_now_on_already_queued_moves_it`: queue [a,b,c], play_now(c, True) → [c,a,b].
  - `test_play_next_after_current`: queue [a,b], play_next(c) → [a,c,b]. play_next(b) → [a,b,c].
  - `test_play_next_empty_queue`: → [c].
  - `test_play_next_ignores_watched`
  - `test_queue_insert_at_clamps`: index 99 → appended. Index 0 → first.
  - `test_save_progress_watched_keeps_in_queue`: 95/100 → `watched_at` set, still in `queue_list`.

  In `tests/test_api.py`:
  - `test_api_play_and_next`: checks the order from the returned JSON.
  - `test_queue_json_has_thumb_duration_ready`
  - `test_undo_with_queue_index_requeues`: queue [a,b]. POST watched on a → [b]. DELETE with `{"queue_index":0}` → [a,b] and a unwatched.
- [ ] **Step 2: Run `pytest tests/test_repo_queue.py tests/test_api.py -q`.** Expected: FAIL (missing attributes and 404s).
- [ ] **Step 3: Implement** the repo functions and API routes listed above. Change `save_progress` to update `watched_at` directly.
- [ ] **Step 4: Run `pytest -q`.** Expected: all pass. Update the v1 test `test_progress_marks_watched_at_90` only if it asserted dequeueing. It doesn't, so it's unchanged.
- [ ] **Step 5: Commit** with `feat(queue): current item, play now / play next, undo re-queues`.

### Task 2: Shell, fonts, navigation, routes

**Files:**
- Create `app/static/css/app.css` (tokens, fonts, base, shell only), `app/static/js/nav.js`, `app/static/fonts/*`, `docs/design/feed-styles.html`.
- Modify `app/templates/base.html` and `app/main.py`.
- Delete `app/templates/queue.html`, `app/templates/watch.html`, `app/static/style.css` and `app/static/app.js` (its logic moves to Tasks 3 and 4).
- Test: `tests/test_pages.py`

**Interfaces:**
- `base.html` structure:
  - `<main id="main" data-title="…">{% block content %}`
  - `<div id="player" hidden>` (bar + expanded, filled by player.js) containing the only `<video id="video" playsinline preload="metadata">`
  - `<div id="toast" hidden>`
  - scripts `nav.js`, `player.js`, `feed.js` with `defer`
  - `<html data-open-video="{{ open_video or '' }}">`
- `nav.js` exposes `window.Nav = { go(url, {replace?}), reload() }` and emits a `nav:loaded` event on `document` after each swap.
  - It intercepts:
    - `click` on `a[href]` that is same-origin, has no `target` and no `download`, and is not `/media/` or `/thumbs/`
    - `submit` on any form without `data-native`
  - It fetches with `{redirect: "follow"}`, uses `response.url` as the new history URL, parses with `DOMParser`, and replaces `#main` and `document.title`.
  - It saves and restores `scrollY` per history entry, and falls back to `location.assign` on errors or non-HTML responses.
- Routes:
  - `/queue` is removed, so it returns 404.
  - `/watch/{id}` renders `feed.html` (the "today" tab) with `open_video=id` if the video is playable. Otherwise it renders the feed with `error="This video is no longer available."` shown as a banner.
- Fonts: get them from npm `@fontsource/geist`, `@fontsource/geist-mono` and `@fontsource/space-grotesk`, latin woff2 for the weights listed in File Structure. Declare them with `@font-face` in app.css and set `font-display: swap`.

- [ ] **Step 1: Write failing tests** in `tests/test_pages.py`. Replace the v1 queue and watch page tests with:
  - `test_queue_page_removed`: GET `/queue` → 404.
  - `test_watch_renders_feed_with_open_video`: `data-open-video="{id}"` is in the HTML, and the page is the feed (contains `Your feed`).
  - `test_watch_expired_shows_unavailable_banner`
  - `test_shell_has_single_video_and_player`: `html.count("<video") == 1` and `id="player"` is present.
  - `test_no_third_party_hosts`: for `/`, `/channels` and `/static/css/app.css`, none of `fonts.googleapis`, `gstatic`, `ytimg` or `youtube.com` appear.
  - `test_fonts_served`: GET `/static/fonts/geist-latin-400-normal.woff2` → 200 with `font/woff2` or `application/octet-stream`.
  - Update `test_static_assets_served` for the new paths.
  - `test_css_respects_hidden_attribute` now reads `app.css`.
- [ ] **Step 2: Run `pytest tests/test_pages.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement** the shell, routes, fonts, the CSS base and `nav.js`, and copy the mockup into `docs/design/`.
- [ ] **Step 4: Run `pytest -q`.** Expected: all pass, with the feed and channels content still on their old markup.
- [ ] **Step 5: Commit** with `feat(ui): persistent shell, in-app navigation, self-hosted fonts`.

### Task 3: Poster Grid feed, Channels restyle, card actions

**Files:**
- Modify `app/templates/feed.html` and `app/templates/channels.html`.
- Create `app/templates/_card.html` and `app/static/js/feed.js`.
- Extend `app/static/css/app.css`.
- Test: `tests/test_pages.py`

**Interfaces:**
- Feed markup, with class names matching mockup B. Every action element carries `data-action` and `data-id`:
  - **Header:** `.hello` with `<small>` (weekday · `N new this week`, where N = the week count), `<h1>Your feed</h1>` and a gear link `href="/channels" aria-label="Channels"`.
  - **Tabs:** `.tabs` with links `?tab=today|week&group=` and `<sup>` counts.
  - **Chips:** `.chips`, plus an `Add all to queue` pill (`data-action="queue-all" data-ids`) shown only when there are videos.
  - **Poster:** `.feature`, the first item in `videos`, with actions `play`, `next` and `queue`.
  - **Grid:** `.grid2` holding the remaining items via `_card.html`. Each card has:
    - `.thumb` (`data-action="play"` when ready)
    - a duration badge
    - an `.add` button (`queue`, or a ✓ icon when `in_queue`)
    - a `⋯` button (`data-action="menu"`) opening a small popover with play, next, queue and watched
    - a progress line when `position_s > 0` and a duration is known (`width: position/duration*100%`)
    - a `Downloading` overlay (status pending/downloading: no `play` action) or a `Retry` chip (failed → `data-action="retry"`)
  - **Empty state:** `<p class="empty">You're all caught up.</p>`
- `feed.js` handles the delegated actions:
  - `queue`, `queue-all`, `next` and `play` call `Player.add(ids)`, `Player.playNext(id)` and `Player.playNow(id)` (defined in Task 4).
  - `watched` remembers the video's queue index from `Player.queueIndex(id)`, POSTs watched, fades the card, and shows a toast with Undo. Undo sends DELETE with `{queue_index}` when the index ≥ 0, then calls `Nav.reload()` so the counts refresh.
  - `retry`, `menu` (toggle popover; close on outside click or Escape), `[data-confirm]` forms (an inline confirm: the first click turns the button into "Confirm?", the second submits) and `[data-autosubmit]`.
- Grid columns: 2 by default, 3 at `min-width: 900px`, 4 at `1200px`.
- Channels page: same tokens, card sections, compact inputs, small pills, a `← Feed` link at the top, and the version footer kept. Forms are unchanged, so they're handled by nav.js.

- [ ] **Step 1: Write failing tests** in `tests/test_pages.py`. Update the existing feed tests (counts now render as `Today<sup>1</sup>`):
  - `test_feed_poster_is_newest_and_grid_has_rest`: 3 ready videos → the first title is inside `class="feature"` and the other two are inside `.grid2`.
  - `test_feed_header_has_week_count_and_gear`
  - `test_card_downloading_has_no_play_action`
  - `test_card_failed_has_retry`
  - `test_card_progress_line_when_partially_watched`: `position_s` 300 of 600 → `width:50%` (or `50.0%`).
  - `test_card_in_queue_shows_check`
  - `test_add_all_hidden_when_empty`
  - `test_channels_has_back_link_and_version`
  - Keep the v1 assertions for "You're all caught up.", group filtering and no youtube hosts, adapted to the new markup.
- [ ] **Step 2: Run `pytest tests/test_pages.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement** the templates, CSS and feed.js.
- [ ] **Step 4: Run `pytest -q`.** Expected: all pass.
- [ ] **Step 5: Commit** with `feat(ui): poster grid feed, card actions, channels restyle`.

### Task 4: Persistent player

**Files:** Create `app/static/js/player.js` and `scripts/e2e_player.mjs`. Extend `app/static/css/app.css` and `scripts/seed_demo.py` (generates 3 real 6-second H.264 clips with ffmpeg when `--clips` is passed).

**Interfaces:**
- `window.Player` methods:
  - `add(ids: number[])`, `playNext(id)`, `playNow(id)` (sends `keep_current = !video.paused || video.ended` → false when playing), `queueIndex(id) -> number` (−1 if not queued), `refresh()`
  - `expand()`, `collapse()`, `skip()` (DELETE `/api/queue/{current}` → load the next one; keeps playing if it was playing), `done()` (POST watched → next), `restart()`
- State:
  - The queue array comes from the server.
  - `currentId` = first item with `ready`.
  - On page load, if `<html data-open-video>` is set: `playNow(id)` (paused, since there's no gesture) and `expand()`. Otherwise current is loaded paused at `position`.
- Bar (mockup B `.mini`): thumbnail disc with a conic progress ring (driven by `timeupdate`), title/channel, queue button with count (`expand()` scrolled to Up next), ⏯.
  - Hidden when the queue is empty.
  - With no ready item, it shows item 1 with `Downloading` and a disabled ⏯, and re-polls `/api/queue` every 30 s until one is ready.
- Expanded view: a fixed full-screen sheet holding the same `<video>`, moved into it with `appendChild`. Moving a playing `<video>` node keeps it playing in Chromium and WebKit, but call `video.play()` after the move if it was playing.
  - It shows the title, channel, ⏮ ⏯ ⏭, ✓ Done and ⌄.
  - **Up next** list with up/down/remove, using the existing endpoints.
  - Collapse on ⌄, on Escape, on a header swipe down > 60px, or on `nav:loaded`.
- Ended: POST watched (`done()` path), then load the next ready item and play it. Error: v1 skip logic, so add it to `skipped`, toast "Couldn't play that one, skipping", and load the next.
- Progress reporting: every 10 s, on pause and on pagehide, via `/api/videos/{id}/progress`.
- Media Session: metadata (title, artist = channel, artwork = thumb) and handlers for `play`, `pause` and `nexttrack` → `skip()`.

- [ ] **Step 1: Write the e2e script first** (`scripts/e2e_player.mjs`, run with `node`). It takes a base URL argument and exits non-zero on any failed check. Checks:
  1. Seeded feed (3 ready clips a, b, c and 1 downloading d). Click `+` on a → the bar is visible and `video.paused === true`.
  2. Click ▶ on the poster (b) → b is playing. `queueIndex(a) === 1`, because the paused a moved to up next.
  3. Navigate to `/channels` via the gear, then `history.back()` → `video.currentTime` keeps increasing, and `document.querySelectorAll('video').length === 1` throughout.
  4. Submit the add-channel form with `not a url` → the error banner shows and the video is still playing.
  5. `playNext(c)` → queue order [b, c, a].
  6. Seek b to its end → c plays, and b is gone from the feed after `Nav.reload()`.
  7. Expand → the sheet is visible and the video is still playing. Press Escape → collapsed.
  8. Screenshots at 390×844 (scale 2) and 1280×800 → `scrollWidth <= innerWidth`.
- [ ] **Step 2: Run it** against `python scripts/seed_demo.py <dir> --clips` plus uvicorn. Expected: FAIL (no `Player`).
- [ ] **Step 3: Implement** player.js and the CSS.
- [ ] **Step 4: Run the e2e script and `pytest -q`.** Expected: all checks pass, and all tests pass. Look at the screenshots yourself.
- [ ] **Step 5: Commit** with `feat(player): persistent pill player with expanded view and media session`.

### Task 5: Docs, review, release 1.1.0

**Files:** `README.md` (feature bullets: player, Play next; remove Queue tab mentions), `CHANGELOG.md` (Unreleased → Added/Changed), `app/__init__.py` (1.1.0 on the release branch only).

- [ ] **Step 1:** Update the README and CHANGELOG. Run `pytest -q`. Expected: pass.
- [ ] **Step 2:** Commit with `docs: player redesign`. Run the final whole-branch review and fix pass, as in executing-plans.
- [ ] **Step 3:** Git-flow: merge into `develop` (no-ff) and push. Check that CI on develop is green (`gh run watch`). Then create `release/1.1.0`, bump the version and date the changelog, merge into `main` and back into `develop`, push both, and ask the owner to create tag `v1.1.0` with `gh release create`. The sandbox can't push tags.
