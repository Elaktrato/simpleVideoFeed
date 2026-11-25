"""FastAPI application: pages, JSON API, media files and background loops."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__, jobs, repo, youtube
from app.config import Settings, load_settings
from app.db import connect, init_db

log = logging.getLogger(__name__)
_YID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HERE = Path(__file__).parent


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_age(ts: int | None, now: int) -> str:
    if not ts:
        return ""
    d = max(0, now - int(ts))
    if d < 3600:
        return f"{max(1, d // 60)}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["duration"] = fmt_duration
templates.env.filters["age"] = fmt_age


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _back(path: str, error: str | None = None) -> RedirectResponse:
    url = path if not error else f"{path}?error={quote(error)}"
    return RedirectResponse(url, status_code=303)


def _group_or_none(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def create_app(
    settings: Settings,
    yt=youtube,
    start_background: bool = True,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    def now() -> int:
        return int(clock())

    def open_db() -> sqlite3.Connection:
        return connect(settings.db_path)

    # ---------- background loops ----------

    def _with_db(fn, *args):
        c = open_db()
        try:
            return fn(c, *args)
        finally:
            c.close()

    async def poll_loop():
        while True:
            try:
                await asyncio.to_thread(_with_db, lambda c: jobs.poll_all(c, settings, yt, now()))
            except Exception:
                log.exception("poll loop error")
            await asyncio.sleep(settings.poll_minutes * 60)

    async def download_loop():
        while True:
            try:
                did = await asyncio.to_thread(_with_db, lambda c: jobs.download_one(c, settings, yt, now()))
            except Exception:
                log.exception("download loop error")
                did = False
            if not did:
                await asyncio.sleep(30)

    async def cleanup_loop():
        while True:
            try:
                await asyncio.to_thread(_with_db, lambda c: jobs.cleanup(c, settings, now()))
            except Exception:
                log.exception("cleanup loop error")
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for d in (settings.data_dir, settings.videos_dir, settings.thumbs_dir):
            d.mkdir(parents=True, exist_ok=True)
        c = open_db()
        init_db(c)
        jobs.recover(c, settings)
        c.close()
        tasks = []
        if start_background:
            tasks.append(asyncio.create_task(poll_loop()))
            tasks.append(asyncio.create_task(cleanup_loop()))
            for _ in range(settings.max_concurrent_downloads):
                tasks.append(asyncio.create_task(download_loop()))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="simpleVideoFeed", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.now = now

    def db() -> Iterator[sqlite3.Connection]:
        c = open_db()
        try:
            yield c
        finally:
            c.close()

    app.state.db_dependency = db

    # ---------- health ----------

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    # ---------- videos API ----------

    @app.post("/api/videos/{video_id}/watched")
    def watched(video_id: int, conn=Depends(db)):
        if repo.get_video(conn, video_id) is None:
            return _err(404, "No such video")
        repo.mark_watched(conn, video_id, now())
        return {"ok": True, "version": __version__}

    @app.delete("/api/videos/{video_id}/watched")
    def unwatched(video_id: int, conn=Depends(db)):
        if repo.get_video(conn, video_id) is None:
            return _err(404, "No such video")
        repo.unmark_watched(conn, video_id)
        return {"ok": True, "version": __version__}

    @app.post("/api/videos/{video_id}/progress")
    async def progress(video_id: int, request: Request, conn=Depends(db)):
        if repo.get_video(conn, video_id) is None:
            return _err(404, "No such video")
        try:
            body = await request.json()
            position, duration = float(body["position"]), float(body.get("duration") or 0)
        except (ValueError, KeyError, TypeError):
            return _err(400, "Expected {position, duration}")
        return {"watched": repo.save_progress(conn, video_id, position, duration, now())}

    @app.post("/api/videos/{video_id}/retry")
    def retry(video_id: int, conn=Depends(db)):
        v = repo.get_video(conn, video_id)
        if v is None:
            return _err(404, "No such video")
        if v["status"] == "failed":
            repo.reset_attempts(conn, video_id)
            repo.set_status(conn, video_id, "pending")
        return {"ok": True, "version": __version__}

    # ---------- queue API ----------

    def _queue_json(conn):
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "channel": r["channel_title"],
                "status": r["status"],
                "position": r["position_s"],
                "src": f"/media/{r['youtube_video_id']}.mp4" if r["file_path"] else None,
            }
            for r in repo.queue_list(conn)
        ]

    @app.get("/api/queue")
    def queue_get(conn=Depends(db)):
        return _queue_json(conn)

    @app.post("/api/queue")
    async def queue_post(request: Request, conn=Depends(db)):
        try:
            ids = [int(i) for i in (await request.json())["video_ids"]]
        except (ValueError, KeyError, TypeError):
            return _err(400, "Expected {video_ids: [int]}")
        repo.queue_add(conn, ids)
        return _queue_json(conn)

    @app.post("/api/queue/{video_id}/move")
    async def queue_move(video_id: int, request: Request, conn=Depends(db)):
        try:
            direction = (await request.json())["direction"]
        except (ValueError, KeyError, TypeError):
            direction = None
        if direction not in ("up", "down"):
            return _err(400, "direction must be 'up' or 'down'")
        repo.queue_move(conn, video_id, direction)
        return _queue_json(conn)

    @app.delete("/api/queue/{video_id}")
    def queue_delete(video_id: int, conn=Depends(db)):
        repo.queue_remove(conn, video_id)
        return _queue_json(conn)

    # ---------- media ----------

    @app.get("/media/{yid}.mp4")
    def media(yid: str, conn=Depends(db)):
        v = repo.get_video_by_yid(conn, yid) if _YID.match(yid) else None
        if v is None or not v["file_path"] or not Path(v["file_path"]).is_file():
            return _err(404, "Not downloaded")
        return FileResponse(v["file_path"], media_type="video/mp4")

    @app.get("/thumbs/{yid}.jpg")
    def thumb(yid: str):
        p = settings.thumbs_dir / f"{yid}.jpg"
        if not _YID.match(yid) or not p.is_file():
            return _err(404, "No thumbnail")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    # ---------- channel & group forms ----------

    @app.post("/channels")
    def add_channel(
        background: BackgroundTasks,
        url: str = Form(""),
        group_id: str = Form(""),
        conn=Depends(db),
    ):
        if not url.strip():
            return _back("/channels", "Paste a channel link first")
        try:
            info = yt.resolve_channel(url, settings.cookies_file)
            cid = repo.add_channel(conn, info, _group_or_none(group_id), now())
        except (youtube.YouTubeError, repo.AlreadyAdded) as e:
            return _back("/channels", str(e))
        background.add_task(
            _with_db, lambda c: jobs.poll_channel(c, settings, repo.get_channel(c, cid), yt, now())
        )
        return _back("/channels")

    @app.post("/channels/{channel_id}/group")
    def channel_group(channel_id: int, group_id: str = Form(""), conn=Depends(db)):
        repo.set_channel_group(conn, channel_id, _group_or_none(group_id))
        return _back("/channels")

    @app.post("/channels/{channel_id}/shorts")
    def channel_shorts(channel_id: int, include: str = Form(""), conn=Depends(db)):
        repo.set_include_shorts(conn, channel_id, include == "1")
        return _back("/channels")

    @app.post("/channels/{channel_id}/delete")
    def channel_delete(channel_id: int, conn=Depends(db)):
        for p in repo.delete_channel(conn, channel_id):
            Path(p).unlink(missing_ok=True)
        return _back("/channels")

    @app.post("/groups")
    def group_create(name: str = Form(""), conn=Depends(db)):
        if not name.strip():
            return _back("/channels", "Group name can't be empty")
        repo.create_group(conn, name)
        return _back("/channels")

    @app.post("/groups/{group_id}/rename")
    def group_rename(group_id: int, name: str = Form(""), conn=Depends(db)):
        if not name.strip():
            return _back("/channels", "Group name can't be empty")
        repo.rename_group(conn, group_id, name)
        return _back("/channels")

    @app.post("/groups/{group_id}/delete")
    def group_delete(group_id: int, conn=Depends(db)):
        repo.delete_group(conn, group_id)
        return _back("/channels")

    # ---------- pages ----------

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def render(request: Request, name: str, conn, **ctx) -> HTMLResponse:
        ctx.setdefault("queue_count", len(repo.queue_list(conn)))
        return templates.TemplateResponse(request, name, {"now": now(), "version": __version__, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def feed_page(request: Request, tab: str = "today", group: str = "", conn=Depends(db)):
        tab = tab if tab in repo.WINDOWS else "today"
        group_id = _group_or_none(group)
        t = now()
        return render(
            request, "feed.html", conn, page="feed", tab=tab, group_id=group_id,
            groups=repo.list_groups(conn),
            videos=repo.feed(conn, tab, group_id, t),
            counts=repo.feed_counts(conn, group_id, t),
        )

    @app.get("/watch/{video_id}", response_class=HTMLResponse)
    def watch_page(request: Request, video_id: int, conn=Depends(db)):
        v = repo.get_video(conn, video_id)
        playable = bool(
            v is not None and v["status"] == "ready" and v["file_path"] and Path(v["file_path"]).is_file()
        )
        in_queue = any(r["id"] == video_id for r in repo.queue_list(conn))
        return render(request, "watch.html", conn, page="watch", video=v, playable=playable, in_queue=in_queue)

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request, conn=Depends(db)):
        items = repo.queue_list(conn)
        return render(request, "queue.html", conn, page="queue", items=items, queue_count=len(items))

    @app.get("/channels", response_class=HTMLResponse)
    def channels_page(request: Request, error: str = "", conn=Depends(db)):
        return render(
            request, "channels.html", conn, page="channels", error=error,
            groups=repo.list_groups(conn), channels=repo.list_channels(conn),
        )

    return app


def get_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
