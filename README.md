# simpleVideoFeed

A small, self-hosted YouTube feed for people who want to watch their channels **without** the endless scroll.

- You add the channels you follow and sort them into groups.
- The **Today** and **This Week** tabs show only those channels' new uploads. There are no recommendations, comments or Shorts rabbit holes.
- New videos are downloaded to your server and play from there, so you never touch youtube.com.
- Watched videos disappear. When you're through, the feed says **"You're all caught up."**
- Videos keep playing while you browse. A player bar at the bottom shows progress; tap it for the full-screen player with ⏮ ⏯ ⏭, **✓ Done** and an **Up next** list you can reorder or remove.
- **Play next** plays a video right after the current one. **+ Queue** adds it to the end. Lock-screen and headphone controls work too.
- Videos are deleted automatically: watched ones after a day, everything 14 days after upload.

## Run it with Portainer

1. In Portainer, go to **Stacks → Add stack**, give it a name, and paste [`docker-compose.yml`](docker-compose.yml).
2. Adjust the `./data` volume path if you want the videos somewhere specific, for example `/srv/simplevideofeed:/data`.
3. Click **Deploy the stack**.
4. Open `http://<your-server>:8080`. Away from home, use the server's Tailscale name or IP, for example `http://homeserver:8080`.
5. Go to **Channels** and paste a channel link (`youtube.com/@name`, a `/channel/UC…` link, or any video link from that channel).

> **First publish only:** after the GitHub Action has pushed the first image, open the repo's **Packages → simplevideofeed → Package settings** and set its visibility to **Public**. Portainer can then pull it without logging in.

### Who can open it

There is no login, by design. With the default `"8080:8080"` port mapping, anyone on your home network, and you over Tailscale, can open it. To make it Tailscale-only, bind the port to the server's Tailscale IP instead: `"100.x.y.z:8080:8080"`.

## Settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `POLL_MINUTES` | `30` | How often channels are checked for new uploads |
| `MAX_HEIGHT` | `720` | Maximum download resolution. 720p is easy on mobile data over Tailscale. |
| `RETENTION_DAYS` | `14` | Every video file is deleted this many days after upload |
| `WATCHED_GRACE_HOURS` | `24` | Watched videos are deleted this long after you finish them |
| `MAX_CONCURRENT_DOWNLOADS` | `1` | Parallel downloads |
| `YTDLP_COOKIES_FILE` | *(unset)* | Path to a `cookies.txt`, only if YouTube starts blocking downloads (see below) |
| `DATA_DIR` | `/data` | Where the database, videos and thumbnails live inside the container |

## Updating

YouTube changes often, and the downloader (yt-dlp) has to keep up. The image is rebuilt automatically **every Monday** with the newest yt-dlp. To update, open the stack in Portainer and choose **Update the stack → Re-pull image**, or recreate the container with "pull latest image" on.

If downloads start failing everywhere ("failed — retry" on every video), re-pulling the image is the first thing to try.

## If YouTube blocks downloads

Some servers get "Sign in to confirm you're not a bot" errors. If that happens:

1. Export your YouTube cookies in Netscape `cookies.txt` format with a browser extension. A throwaway Google account is a good idea.
2. Put the file in your data folder, for example `./data/cookies.txt`.
3. Set `YTDLP_COOKIES_FILE: /data/cookies.txt` in the stack and redeploy.

## How it works

- Every channel's public RSS feed is checked every `POLL_MINUTES`. No API key is needed. Shorts and live streams are skipped (Shorts can be turned on per channel).
- When you add a channel, its last 7 days of uploads are pulled in. Nothing older than a week is ever shown.
- Videos download one at a time as H.264/AAC MP4 (≤ `MAX_HEIGHT`) so they play on phones, including iPhone, and on laptops.
- "Watched" means you played 90% of it or tapped **✓**. Playback position is saved, so you can resume on another device.

## Versions and branches

This repo uses **git-flow**:

| Branch / tag | Purpose | Docker image tag |
|---|---|---|
| `main` | Released code only; every merge is a release | — |
| `vX.Y.Z` tags on `main` | A release | `X.Y.Z`, `X.Y`, `X`, `latest` |
| `develop` | Integration branch for the next release | `develop` |
| `feature/*` | One branch per feature, merged into `develop` | — |
| `release/X.Y.Z` | Release prep (version bump, changelog), merged into `main` and back into `develop` | — |
| `hotfix/X.Y.Z` | Urgent fix off `main`, merged into `main` and `develop` | — |

Portainer should normally track `latest`, which only moves on a release or the weekly yt-dlp rebuild. Pin a version such as `ghcr.io/elaktrato/simplevideofeed:1.0.0` if you want nothing to change under you. The version is shown at the bottom of the **Channels** page and at `/healthz`.

To cut a release:

```bash
git checkout develop && git checkout -b release/1.1.0
# bump __version__ in app/__init__.py, move "Unreleased" notes in CHANGELOG.md under 1.1.0, commit
git checkout main && git merge --no-ff release/1.1.0 && git tag -a v1.1.0 -m "v1.1.0"
git checkout develop && git merge --no-ff release/1.1.0 && git branch -d release/1.1.0
git push origin main develop --follow-tags
```

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
DATA_DIR=./data .venv/bin/uvicorn app.main:get_app --factory --reload --port 8080
```

`scripts/seed_demo.py <dir>` fills a data folder with fake channels for UI work.
