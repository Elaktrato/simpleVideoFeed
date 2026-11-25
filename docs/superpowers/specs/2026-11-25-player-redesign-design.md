# simpleVideoFeed — Persistent Player & "Poster Grid" Redesign

*Date: 2026-11-25. Builds on v1.0.1 (`docs/superpowers/specs/2026-11-25-simplevideofeed-design.md`).*
*Visual reference: mockup B "Poster Grid" at https://claude.ai/artifact/U85aWFnA99vhQPhsMZXzN2. Its HTML/CSS is the source of truth for the look.*

## Intent

The owner finds the current UI plain ("like someone just barely learned CSS"): the buttons and tabs are large and flat. Watching also stops whenever they leave the video page. They want the app to behave like YouTube's player:

- A video keeps playing while they browse.
- They can stack up what to watch next while something plays.
- The whole thing looks sleek and modern.

The anti-scroll goals of v1 still apply: a finite feed, no recommendations, nothing loaded from YouTube.

## Goals

### 1. Persistent player (owner's requirements)
- Once a video is playing, it keeps playing while the user moves between pages (Feed tabs, groups, Channels).
- A **player bar** sits at the bottom of every page whenever the queue isn't empty.
- **The queue tab and `/queue` page go away.** The queue lives in the player.
- **Play next** inserts a video right after the one currently loaded. **Add to queue** appends it to the end.
- **Adding to an empty queue** loads the video into the bar **paused**. Nothing starts automatically.
- **▶ Play on a feed item while another plays** switches to it immediately (owner confirmed). The old video:
  - leaves the queue,
  - keeps its saved position,
  - is *not* marked watched,
  - stays in the feed, showing a partial-progress bar, so it can be resumed.
- When a video ends, it is marked watched (as in v1), leaves the queue, and the next one plays. When the queue empties, the bar disappears.

### 2. Expanded player (my proposal, accepted in conversation)
- Tapping the bar opens a **full-screen player**. It contains:
  - a big video with native controls,
  - the title and channel,
  - ⏮ restart / ⏯ / ⏭ skip,
  - ✓ Done (mark watched and skip),
  - an **Up next** list where items can be reordered (up/down) or removed.
- It collapses back to the bar with a ⌄ button, the Escape key, a downward swipe on the header area, or when the user follows any link.
- The **same `<video>` element** is used in both states and is never recreated, so playback is never interrupted.

### 3. Look: mockup B "Poster Grid" (owner's choice)
- **Fonts:** Geist for the UI, Space Grotesk for headings, Geist Mono for numbers and durations.
- **Colors:** Tokyo Night as today, with a subtle radial glow top-left.
- **Feed header:** a small date line ("Wednesday · 9 new this week"), the big title "Your feed", and a gear icon linking to Channels in place of the old nav bar.
- **Tabs:** text tabs Today / This Week with a superscript count and a blue→purple underline on the active tab.
- **Group chips:** small rounded chips, with the active one tinted purple.
- **Poster:** the newest video in the current tab and group gets a 16:10 poster card with a dark gradient over the thumbnail. It shows the channel, age and duration in small caps, the title in Space Grotesk, and pill buttons **▶ Play**, **Play next** and **+ Queue**.
- **Grid:** the rest is a two-column grid on phones and more columns on wider screens (about 3 at 900px+ and 4 at 1200px+).
  - Each card shows the thumbnail, a duration badge, a **+** button in the corner (Add to queue), the title (2 lines) and channel · age.
  - **My call:** each card also has a **⋯** button opening a small menu: Play, Play next, Add to queue, Mark watched. Phones have no hover, and a long-press alone is undiscoverable.
  - Tapping the thumbnail = Play.
  - Already-queued cards show a ✓ in place of the **+**.
- **Pill player bar:** a floating rounded pill above a bottom fade. From left to right:
  - a round thumbnail with a **progress ring** around it,
  - the title and channel,
  - a queue button with the queue count (opens the expanded player scrolled to Up next),
  - a round ⏯ button.
- **Video states on cards:**
  - Downloading: the thumbnail is dimmed with a small "Downloading" label, and Play is disabled. Play next and + Queue still work.
  - Failed: a "Retry" chip.
  - Partially watched: a thin progress line along the bottom of the thumbnail.
- **Empty feed:** keeps "You're all caught up.", restyled as a calm centered illustration-free message in the new type.
- **"Add all to queue"** stays as a small pill next to the chips, shown only when the view has items.
- **✓ / Undo toast:** same behaviour, restyled as a pill above the player bar.
- **Channels page** is restyled in the same language: cards, small pills, compact inputs, a ← back link to the feed. Its functions are unchanged.

## Non-goals
- No change to downloading, polling, cleanup, data retention or the JSON shapes, beyond what the queue needs.
- No SPA framework or front-end build step.
- No light theme (Tokyo Night only, as before).
- No smart-TV or remote-control (D-pad) navigation.

## Anti-goals
- Playback stops or restarts when navigating within the app.
- Anything autoplays except the next queue item after one ends.
- Anything loads from YouTube or Google at runtime. Fonts are bundled, see Constraints.
- The redesign adds infinite scroll or "more" sections.

## Constraints
- Single user. Queue and positions stay server-side, so a queue started on the phone continues on a laptop.
- Must work in iOS Safari: `playsinline`, autoplay only after a user gesture (the first ▶ tap). Subsequent auto-advance in the same `<video>` is allowed.
- **Fonts are self-hosted** as woff2 in `app/static/fonts/` (Geist, Geist Mono, Space Grotesk, Latin subsets). The app sits behind Tailscale, and "nothing from third parties at runtime" holds. *My call.*
- Keep the page usable at 360px width with no horizontal scroll.

## Approaches considered (owner picked A)
- **A. Client-side content swapping. Chosen.** Pages stay server-rendered by Jinja.
  - A small script intercepts same-origin link clicks and form submits, fetches the target page and replaces only `<main>`.
  - It updates the title and history (`pushState`, and `popstate` for Back).
  - The player and bar live outside `<main>` and are never replaced.
  - Existing page routes and tests stay. The new parts are roughly 100 lines of navigation JS plus the player.
- **B. SPA rewrite (React etc.).** Rejected: a full rewrite plus a build step for the same result.
- **C. Pages inside an iframe under a fixed player.** Rejected: the URL and Back stop matching the page, scrolling is awkward on phones, and it feels hacky.

## Design

### Queue model (server)
- The queue table keeps its ordering. **Item 1 is "current"**: the video loaded in the player, playing or paused.
- New repo operations:
  - `queue_play_now(video_id)`: makes the video item 1 and removes the old item 1 from the queue. The old video's `position_s` is already saved by progress reports, and it isn't marked watched.
  - `queue_play_next(video_id)`: inserts at position 2, or position 1 if the queue is empty. If the video is already queued, it moves there.
  - `queue_add(ids)`: unchanged; it appends.
- New API endpoints:
  - `POST /api/queue/play` with `{video_id}`
  - `POST /api/queue/next` with `{video_id}`
  - The existing `POST /api/queue`, move and delete endpoints stay.
  - All return the queue JSON, which is unchanged apart from adding `thumb` (local thumbnail URL or null) and `duration`.
- Skipping (⏭) removes item 1 from the queue **without** marking it watched. ✓ Done marks it watched, which already removes it from the queue. ⏮ restart seeks to 0.
- Removed: the `/queue` page route and `queue.html`. `/watch/{id}` stays as a deep link: it renders the feed page and tells the player to play that video. Expired or missing videos show the existing "no longer available" message as a toast.

### Front end
- `base.html` holds the persistent shell: `<main>` (swapped), the player bar, the expanded-player overlay, the single `<video>`, and the toast.
- **Navigation script:**
  - It intercepts clicks on same-origin `<a>` links without `target` or `download`, and `<form>` submits.
  - It fetches with `fetch()` and follows redirects, so the 303 redirects from the Channels forms work. It parses with `DOMParser`, replaces `<main>` and `<title>`, and scrolls to top, or restores the scroll position on Back.
  - It falls back to a full load on a network error or a non-HTML response.
  - Page scripts are written as delegated handlers, so swapped content needs no re-initialisation.
- **Player module:**
  - On first load it fetches `/api/queue` and loads item 1 **paused** at its saved position.
  - It renders the bar and expanded view from queue JSON, and re-fetches the queue after every queue action.
  - It reports progress as today (every 10s, on pause, on ended, on pagehide).
  - It skips unplayable items as in v1.0.1.
  - It sets **Media Session** metadata and handlers (play, pause, next track) so phone lock-screen and headphone controls work. *My call; small.*
- Feed cards update in place after queue actions (the **+** becomes ✓). Tab and queue counts refresh by re-fetching the current page's `<main>` silently after ✓ / Undo. *My call: this fixes the stale-count minor from v1.*

## Testing
- **Server:** repo and API tests for play-now, play-next (including when already queued and when the queue is empty), skip, and the queue JSON fields. Page tests:
  - The feed renders the poster and grid, the gear link, and no queue nav.
  - `/queue` is gone (404).
  - `/watch/{id}` renders the feed with a play directive.
  - The Channels page renders.
  - The CSS has no YouTube hosts and the fonts are served locally.
- **Browser (Playwright, as for v1)** using real H.264 clips:
  1. Play from the feed, navigate to Channels and back: `currentTime` keeps increasing and the `<video>` is the same element.
  2. Play next inserts after current, and Add to queue appends.
  3. Add to an empty queue: the bar appears and the video is paused.
  4. Play on another item: it switches, and the old item leaves the queue without being marked watched.
  5. The video ends: the next one plays and the old one disappears from the feed.
  6. Expand and collapse.
  7. Screenshots at 390px and 1280px: no horizontal scroll.

## Left to the builder
- Exact spacing and animation timings within the mockup's look, swipe-gesture thresholds, the scroll-restore approach, the module layout of the JS, and icon drawing.

## Decisions after builder review (my calls, pending owner's read)
- **"Current" = the first *downloaded* item in the queue.** Items still downloading keep their place and are passed over until they're ready. If nothing queued is ready, the bar shows item 1 with "Downloading…" and ⏯ disabled. Play next inserts right after current.
- **90% still marks a video watched** (v1 rule), but the *current* item stays in the queue until `ended`, ✓ Done or ⏭. `save_progress` stops removing the current item from the queue, and the queue JSON includes it even when it's watched.
- **`/watch/{id}`** renders the feed with the expanded player open and that video loaded **paused**, because iOS needs a tap. It uses the same queue rule as ▶ Play.
- **Undo after ✓** restores the video to its previous queue position: the client sends the position it had. ✓ on the current item from a card behaves like ✓ Done and advances.
- **OPEN (owner):** what ▶ Play does to a loaded but paused or unstarted current item.
