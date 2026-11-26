# Unfollow from Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner unfollow a channel from the full-screen player or a card's ⋯ menu, keeping the playing video until it ends.

**Architecture:** The server soft-unfollows the channel (`channels.unfollowed_at`): it is hidden everywhere and its other videos are dequeued at once. `jobs.cleanup` later hard-deletes unfollowed channels that have nothing left in the queue. The player and the feed call one new API endpoint.

**Tech Stack:** FastAPI, SQLite, Jinja, vanilla JS. pytest. Playwright e2e (`scripts/e2e_player.mjs`).

**Spec:** `docs/superpowers/specs/2026-11-26-unfollow-from-player-design.md`

## Global Constraints

- The playing video is never interrupted by unfollowing its channel. It plays until it ends, ⏭ or Done.
- Two-tap confirm: the first tap changes the label to `Unfollow?`, and the second tap within 4 s confirms. This matches the existing feed.js inline confirm.
- Toast copy: `Unfollowed <channel title>`.
- Existing databases must upgrade in place on startup, with no manual step.
- No undo toast. Re-adding the channel on the Channels page re-follows it.
- The single `<video>` is never recreated. Nothing autoplays except queue advance and taps (unchanged invariants).

## Review Focus

1. **Startup on a live v1.1.0 database** that has no `unfollowed_at` column: the app starts, and nothing is lost. Tested in Task 1 (`test_init_db_migrates_old_channels_table`).
2. **Unfollowing a channel whose video is current but paused, while other channels' items are ahead in the queue:** only that channel's other items leave the queue, and the order of the rest is unchanged. Tested in Task 1 (`test_unfollow_keeps_other_channels_order`).
3. **Cleanup runs while the kept video is still queued:** nothing is deleted until it leaves the queue. Tested in Task 1.
4. **Unfollowing from a card when nothing from that channel is playing:** `keep_video_id` is null and every item of the channel is dequeued. Tested in Task 1 (API) and Task 2 (e2e).
5. **A channel that is re-added before cleanup** comes back with its videos visible, and is not deleted later by cleanup. Tested in Task 1 (`test_refollow_clears_unfollowed_and_survives_cleanup`).

---

### Task 1: Server: soft-unfollow, filters, cleanup, API

**Files:**
- Modify `app/db.py` (column and migration)
- Modify `app/repo.py`
- Modify `app/jobs.py` (`poll_all`, `cleanup`)
- Modify `app/main.py` (route, `_queue_json`, `add_channel` re-follow)
- Test `tests/test_unfollow.py` (new)

**Interfaces (produced):**
- **Schema:** `channels.unfollowed_at INTEGER` (nullable), in `SCHEMA`. `init_db` runs `ALTER TABLE channels ADD COLUMN unfollowed_at INTEGER` when `PRAGMA table_info(channels)` lacks it.
- `repo.unfollow_channel(conn, channel_id: int, keep_video_id: int | None, now: int) -> bool`:
  - Returns False if the channel doesn't exist.
  - Otherwise sets `unfollowed_at = now` and deletes queue rows for the channel's videos where `video_id != keep_video_id`.
  - `keep_video_id` is ignored if it isn't one of that channel's videos.
- **Unfollowed channels are filtered out:**
  - `repo.list_channels` and `repo._feed_where` (feed and counts) skip channels with `unfollowed_at IS NOT NULL`.
  - `repo._queueable` returns False for videos of unfollowed channels. That covers `queue_add`, `queue_play_now`, `queue_play_next` and `queue_insert_at`.
  - `repo.get_channel` stays unfiltered.
- `repo.refollow_channel(conn, youtube_channel_id: str, group_id: int | None) -> int | None`:
  - If an unfollowed channel with that id exists, it clears `unfollowed_at`, sets `group_id` and returns its id.
  - Otherwise it returns None.
- `repo.unfollowed_channels_ready_to_delete(conn) -> list[int]`: unfollowed channel ids with no queued videos.
- **Jobs:**
  - `jobs.poll_all` skips unfollowed channels, because it uses `list_channels`.
  - `jobs.cleanup` finally calls `repo.delete_channel` for each id from `unfollowed_channels_ready_to_delete` and unlinks the returned paths, tolerating missing files. Those deletions are added to the returned count.
- **API:**
  - `POST /api/channels/{channel_id}/unfollow` takes the JSON body `{keep_video_id: int|null}` (the body is optional) and returns `_queue_json(conn)`. An unknown channel returns 404 `{"error": "No such channel"}`.
  - `_queue_json` items gain `"channel_id": r["channel_id"]`.
  - `POST /channels` (add form): call `refollow_channel` first. If it returns an id, redirect to `/channels` with no error and run the background poll for that id. Otherwise do the existing add.

- [ ] **Step 1: Write failing tests** in `tests/test_unfollow.py`, using `make_channel` and `make_video` from `tests/helpers.py` and the `env` fixture pattern from `tests/test_api.py`:
  - `test_init_db_migrates_old_channels_table`: create a connection, run the old `CREATE TABLE channels (...)` without the column, then run `init_db`. Assert `unfollowed_at` is in the `PRAGMA table_info` names and that an existing row survives.
  - `test_unfollow_dequeues_all_but_kept`: channel A has videos a1, a2, a3 (ready). Queue [a1, a2, a3]. `unfollow_channel(A, keep_video_id=a1)` gives a queue of [a1].
  - `test_unfollow_keeps_other_channels_order`: queue [b1, a1, b2, a2]. Unfollow A, keeping a1, gives [b1, a1, b2].
  - `test_unfollow_ignores_keep_from_other_channel`: queue [a1, b1]. Unfollow A with keep=b1 gives [b1].
  - `test_unfollowed_hidden_from_feed_counts_and_list`: `feed`, `feed_counts` and `list_channels` exclude channel A after unfollowing.
  - `test_unfollowed_videos_not_queueable`: `queue_add`, `queue_play_next` and `queue_play_now` with a2 after unfollowing A leave the queue unchanged.
  - `test_poll_all_skips_unfollowed`: uses `FakeYT` from `tests/fakeyt.py`. Nothing is requested for A's feed URL.
  - `test_cleanup_deletes_unfollowed_only_when_nothing_queued`: write a file for a1. While a1 is still queued, cleanup keeps channel A and the file. After `queue_remove(a1)`, cleanup deletes the channel and its videos, and unlinks the file.
  - `test_refollow_clears_unfollowed_and_survives_cleanup`: unfollow A, then `refollow_channel(A.youtube_channel_id, group_id=g)` returns A's id with `group_id == g`. Cleanup doesn't delete it, and its videos are back in `feed`.
  - `test_api_unfollow_returns_queue_and_404`: POST with `{"keep_video_id": a1}` returns 200 JSON whose ids are [a1] and whose items have `channel_id`. POST to `/api/channels/999/unfollow` returns 404.
  - `test_add_form_refollows_unfollowed_channel`: FakeYT resolves the URL to A's channel id. POST `/channels` gives a 303 to `/channels` without `error`, and A is listed again.
- [ ] **Step 2: Run `.venv/bin/pytest tests/test_unfollow.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run `.venv/bin/pytest -q`.** Expected: all pass.
- [ ] **Step 5: Commit** with `feat(channels): soft-unfollow with deferred cleanup`.

### Task 2: UI: Unfollow in the player sheet and the card menus

**Files:**
- Modify `app/templates/base.html` (sheet meta)
- Modify `app/templates/_card.html` and `app/templates/feed.html` (⋯ menu / poster)
- Modify `app/static/js/player.js` and `app/static/js/feed.js`
- Modify `app/static/css/app.css`
- Modify `scripts/e2e_player.mjs` and `scripts/seed_demo.py` (if needed)
- Test: `tests/test_pages.py` and the e2e script

**Interfaces:**
- Consumes `POST /api/channels/{id}/unfollow` and `channel_id` in the queue JSON from Task 1.
- **Sheet:**
  - `.sh-meta .ch` becomes a row: the channel name, then `<button type="button" class="unfollow" data-p="unfollow">Unfollow</button>`.
  - The button is hidden when there is no current item.
- **Player:** adds `Player.unfollow(channelId: number) -> Promise`.
  - It sends `keep_video_id` = the current id if the current item's `channel_id === channelId` (else null).
  - It applies the returned queue through the existing `op()`/`apply()` path without interrupting playback. The current item stays current.
  - It shows `Toast.show("Unfollowed " + channelTitle)` and calls the existing `quietReload()` so the feed and counts refresh.
- **Cards:**
  - Every card's ⋯ menu, and the poster's actions, get `<button type="button" data-action="unfollow" data-channel="{{ v.channel_id }}" data-name="{{ v.channel_title }}">Unfollow channel</button>`.
  - `feed.js` handles it with the two-tap confirm: the first tap sets the label to `Unfollow?` and an `armed` class, which reset after 4 s. The second tap calls `window.Player?.unfollow(channelId)`, or falls back to POSTing the API and `Nav.reload()`.
- **Sheet button:** uses the same two-tap confirm inside player.js.
- `repo.feed` rows need `channel_id`. They already select `v.*`, so `v.channel_id` is available.

- [ ] **Step 1: Write failing tests.**
  - `tests/test_pages.py`:
    - `test_card_menu_has_unfollow`: a ready video's card contains `data-action="unfollow"` and `data-channel="{channel_id}"`.
    - `test_player_sheet_has_unfollow_button`: `data-p="unfollow"` is in `/`.
  - e2e (new checks after the existing ones, on a fresh seed):
    - (a) Queue b and c, where c is from b's channel or a second clip from that channel; add one to the seed if needed. Play b, expand, tap Unfollow twice → b is still playing (`currentTime` increasing, same element), the queue holds only b, and the toast text starts with `Unfollowed`.
    - (b) After b ends, nothing from that channel plays and the feed no longer shows that channel's cards.
    - (c) A card ⋯ → Unfollow channel → first tap shows `Unfollow?`, and the queue is unchanged until the second tap.
- [ ] **Step 2: Run the tests and see them fail.** Run `.venv/bin/pytest tests/test_pages.py -q`, and the e2e as `NODE_PATH=$(npm root -g) DATA_DIR=<seed> node scripts/e2e_player.mjs http://127.0.0.1:<port>` after seeding with `--clips` and starting uvicorn.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full pytest and the full e2e.** Expected: all pass.
- [ ] **Step 5: Commit** with `feat(ui): unfollow channel from player and card menu`.

### Task 3: Docs, review, release 1.2.0

- [ ] **Step 1:** README: add a feature bullet, "Unfollow a channel straight from the player or a video's ⋯ menu". CHANGELOG `[Unreleased]` → `### Added`. Commit with `docs: unfollow from player`.
- [ ] **Step 2:** Run the final whole-branch review and one fix wave, per the execution skill.
- [ ] **Step 3:** Git-flow:
  - Merge into `develop` and push, and wait for green CI.
  - Create `release/1.2.0`: bump `__version__` to 1.2.0 and date the changelog.
  - Merge into `main` and `develop`, and push both.
  - The owner then creates tag `v1.2.0` with `gh release create`.
