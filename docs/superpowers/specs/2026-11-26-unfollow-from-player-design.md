# Unfollow a channel from the player: short design

*Date: 2026-11-26. Target release: v1.2.0.*

**Intent.** While watching a video, the owner often realises they don't want that channel any more. They should be able to unfollow it right there, without going to the Channels page.

**Behaviour (owner approved).**
- An **Unfollow** button sits next to the channel name in the full-screen player, and **Unfollow channel** is added to every card's ⋯ menu. Both need a second tap to confirm ("Unfollow?" → tap again), like the other destructive buttons.
- **On confirm:**
  - The channel's other videos disappear from the feed and the queue immediately.
  - The channel disappears from the Channels list and is no longer polled.
  - A toast shows "Unfollowed <channel>".
- **The video that is playing keeps playing to the end** (or until ⏭ / Done). After that it and the channel's files are cleaned up.

**Approach (owner picked A).**
- **A. Soft-unfollow on the server, hard-delete later in cleanup. Chosen.** It works even if the app is closed mid-video.
- **B. The player defers the existing delete until the video ends.** Rejected: the delete is lost or mistimed if the tab closes or the owner switches devices.

**Design.**
- **Database:** new column `channels.unfollowed_at INTEGER NULL`. `init_db` adds the column to existing databases with `ALTER TABLE … ADD COLUMN` when it is missing. Live installs already have data, so this migration is required.
- **New repo function** `unfollow_channel(conn, channel_id, keep_video_id: int | None, now) -> None`. It sets `unfollowed_at` and removes the channel's videos from the queue, except `keep_video_id`.
- **Unfollowed channels are filtered out in four places:**
  - `list_channels`
  - `feed` and `feed_counts`
  - `poll_all`, which skips them
  - `queue_add`, `queue_play_now`, `queue_play_next` and `queue_insert_at`, which refuse their videos
- **Cleanup:** `jobs.cleanup` hard-deletes every unfollowed channel that has no queued videos left, reusing `delete_channel`. It also deletes the returned video and thumbnail files.
- **API:** `POST /api/channels/{id}/unfollow` with body `{keep_video_id}` returns the queue JSON. An unknown channel returns 404. Queue JSON items gain `channel_id`.
- **Player:** sends `keep_video_id` = the current video **only if it belongs to that channel**, then applies the returned queue without interrupting playback.
- **Feed:** after unfollowing from a card, the page reloads quietly so counts refresh.
- **Re-following (my call):** adding a channel that is unfollowed but not yet cleaned up re-follows it: it clears `unfollowed_at` and applies the chosen group. It does not raise "already added".

**Non-goals.** No undo toast. Re-adding the channel on the Channels page is the undo. No change to the Channels page Remove, which still deletes immediately.

**Tests.**
- pytest:
  - the migration on an old schema
  - `unfollow_channel` keeps only `keep_video_id` in the queue
  - the feed, channel list and poll skip unfollowed channels
  - the queue functions refuse their videos
  - cleanup hard-deletes only once nothing is queued, and unlinks the files
  - re-follow
  - the API returns 404 and the queue JSON with `channel_id`
- e2e: while b plays, unfollow b's channel from the sheet. b keeps playing, the channel's other queued video leaves Up next, and after b ends the next item plays.
