// Persistent player (window.Player): the pill bar, the expanded sheet and the single <video>.
// The <video> is rendered once by base.html and only ever moved (appendChild), never recreated.
(() => {
  const root = document.getElementById("player");
  const video = document.getElementById("video");
  if (!root || !video) return;
  const $ = (sel) => root.querySelector(sel);
  const mini = $(".mini");
  const sheet = $(".sheet");
  const stage = $(".stage");
  const hold = $(".vhold");
  const list = $(".upnext ol");
  const POLL_MS = 30000;
  const PROGRESS_MS = 10000;
  const SEEK_STEP = 10;    // default Media Session seek offset, seconds
  const RESYNC_S = 3;      // a paused video further than this from the server position gets moved

  let queue = [];          // from /api/queue, in order
  let loadedId = null;     // id whose file is in <video>
  const skipped = new Set(); // ids that failed to play this session (v1 skip logic)
  const gone = new Set();  // ids dropped locally whose server removal is still in flight
  let chain = Promise.resolve(); // serialises queue requests so responses apply in order
  let pollTimer = 0;
  let quietNav = false;    // our own Nav.reload() must not collapse the sheet
  let lastReport = 0;      // Date.now() of the last progress POST
  let lastPos = 0;         // position the server has (or will have) for loadedId
  let inflight = 0;        // progress POSTs not answered yet
  let wantPlay = false;    // user intent: play (load/play/▶) vs pause (user pause). Survives reloads.
  let switching = false;   // a new src is loading: its pause/emptied events are not user intent
  let lastTime = 0;        // last good currentTime of loadedId (for the network-error retry)
  let retry = null;        // {id, at}: the one in-place reload after a network error
  let expectPop = false;   // our own history.back() after collapsing the sheet

  const toast = (msg, opts) => window.Toast?.show(msg, opts);

  async function api(method, url, body, { keepalive = false } = {}) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      keepalive,
    });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }
  // Retry a request with backoff (a phone in the background can drop the odd request).
  async function retrying(fn, tries = 4) {
    for (let n = 0; ; n++) {
      try {
        return await fn();
      } catch (e) {
        if (n + 1 >= tries || /^4\d\d$/.test(e.message)) throw e;
        await new Promise((r) => setTimeout(r, 1000 * 2 ** n));
      }
    }
  }
  // Queue an API call; its JSON (the queue, unless `getQueue` says otherwise) becomes the new state.
  function op(fn, { autoplay, resync = false } = {}) {
    const run = chain.then(async () => {
      const wasPlaying = !video.paused && !video.ended;
      const q = await fn();
      apply(q, autoplay === undefined ? wasPlaying : autoplay, { resync });
      return q;
    });
    chain = run.catch(() => {});
    return run;
  }
  const getQueue = () => api("GET", "/api/queue");

  const playable = (i) => i.ready && i.src && !skipped.has(i.id);
  const current = () => queue.find(playable) || null;
  const indexOf = (id) => queue.findIndex((i) => i.id === id);
  const isDownloading = (i) => !i.ready && (i.status === "pending" || i.status === "downloading");

  // ---- state -> <video> ----
  function apply(q, autoplay, { resync = false } = {}) {
    if (Array.isArray(q)) queue = q.filter((i) => !gone.has(i.id));
    const cur = current();
    if (!cur) {
      if (loadedId !== null) unload();
    } else if (cur.id !== loadedId) {
      load(cur, autoplay);
    } else if (resync) {
      resyncPosition(cur);
    }
    render();
    schedulePoll();
    // The server's "current" is the first *ready* item. Pin the loaded one to the head so a
    // downloading item ahead of it that finishes later can't take over and yank playback.
    if (cur && indexOf(cur.id) > 0) pin(cur.id);
  }

  let pinning = null;
  function pin(id) {
    if (pinning === id) return;
    pinning = id;
    const done = () => { if (pinning === id) pinning = null; };
    op(() => api("POST", "/api/queue/play", { video_id: id, keep_current: true })).then(done, done);
  }

  // A tab that sat in the background (or another device) may be behind the server: if the same
  // item is loaded and paused, follow the server's position.
  function resyncPosition(item) {
    if (!video.paused || switching || inflight > 0 || video.readyState < 1) return;
    if (Math.abs(video.currentTime - lastPos) > 1) return; // unsaved local seek: keep it
    const pos = Number(item.position) || 0;
    const d = video.duration;
    if (!(Math.abs(pos - video.currentTime) > RESYNC_S)) return;
    if (Number.isFinite(d) && pos >= d - 2) return;
    video.currentTime = pos;
    lastPos = lastTime = pos;
  }

  function unload() {
    report();
    loadedId = null;
    retry = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    switching = false;
    if (navigator.mediaSession) navigator.mediaSession.metadata = null;
  }

  function load(item, autoplay) {
    if (loadedId !== null && !video.ended) report();
    loadedId = item.id;
    retry = null;
    const start = item.position && item.duration && item.position < item.duration - 2 ? item.position : 0;
    lastPos = lastTime = start;
    switching = true;
    setSrc(item.src, item.id, start);
    wantPlay = !!autoplay;
    if (autoplay) video.play().catch(() => {});
    setMediaSession(item);
  }
  // (Re)load src; seek to `at` once metadata is in. Used for new items and the network retry.
  function setSrc(src, id, at) {
    video.src = src;
    if (at > 0) {
      video.addEventListener("loadedmetadata", () => {
        if (loadedId === id && video.currentTime < at - 0.5) video.currentTime = at;
      }, { once: true });
    }
  }

  // ---- Media Session (lock screen, notification, headphones) ----
  const ms = navigator.mediaSession;
  function setMediaSession(item) {
    if (!ms || typeof MediaMetadata === "undefined") return;
    try {
      ms.metadata = new MediaMetadata({
        title: item.title,
        artist: item.channel || "",
        artwork: item.thumb ? [{ src: new URL(item.thumb, location.href).href, type: "image/jpeg" }] : [],
      });
    } catch (e) { /* ignore */ }
  }
  function positionState() {
    if (!ms || !ms.setPositionState) return;
    const d = video.duration;
    try {
      if (loadedId === null || !Number.isFinite(d) || d <= 0) return;
      ms.setPositionState({
        duration: d,
        playbackRate: video.playbackRate > 0 ? video.playbackRate : 1,
        position: Math.min(Math.max(video.currentTime || 0, 0), d),
      });
    } catch (e) { /* ignore */ }
  }
  function seekTo(t) {
    if (loadedId === null) return;
    const d = video.duration;
    video.currentTime = Math.max(0, Number.isFinite(d) ? Math.min(t, d) : t);
  }

  // ---- progress ----
  // Skips ids that are no longer queued (a stale tab must not overwrite what another device saved)
  // and positions the server already has.
  function report(keepalive = false) {
    if (loadedId === null || switching || !video.currentSrc || !(video.duration > 0)) return;
    if (indexOf(loadedId) < 0) return;
    const position = video.currentTime;
    if (Math.abs(position - lastPos) < 0.5) return;
    lastPos = position;
    lastReport = Date.now();
    inflight++;
    fetch(`/api/videos/${loadedId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position, duration: video.duration }),
      keepalive,
    }).catch(() => {}).finally(() => { inflight--; });
  }
  window.addEventListener("pagehide", () => report(true));
  // Android rarely fires pagehide: report when the page goes to the background and re-sync on
  // return. Nothing here pauses: playback continues in the background.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") report(true);
    else refresh({ resync: true }).catch(() => {});
  });
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) refresh({ resync: true }).catch(() => {});
  });

  // ---- rendering ----
  const ICON = (name) => `<svg class="i fill"><use href="#i-${name}"/></svg>`;
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function iconBtn(name, label, action, id, disabled) {
    const b = el("button", "ic");
    b.type = "button";
    b.dataset.p = action;
    b.dataset.id = id;
    b.setAttribute("aria-label", label);
    b.disabled = !!disabled;
    b.innerHTML = `<svg class="i"><use href="#i-${name}"/></svg>`;
    return b;
  }

  // Why an item can't play right now (null when it can).
  function unplayable(i) {
    if (i.ready && i.src) return skipped.has(i.id) ? "Couldn't play" : null;
    if (i.status === "pending" || i.status === "downloading") return "Downloading";
    if (i.status === "failed") return "Download failed";
    return "Unavailable";
  }

  function render() {
    root.hidden = queue.length === 0;
    if (!queue.length) {
      collapse();
      return;
    }
    const cur = current();
    const shown = cur || queue.find((i) => !skipped.has(i.id)) || queue[0];
    const status = cur ? shown.channel : unplayable(shown);
    $(".mini .t").textContent = shown.title;
    $(".mini .s").textContent = status;
    const img = $(".mini .disc img");
    if (shown.thumb) {
      if (img.getAttribute("src") !== shown.thumb) img.src = shown.thumb;
      img.hidden = false;
    } else {
      img.hidden = true;
    }
    $(".sh-meta .t").textContent = shown.title;
    $(".sh-meta .ch").textContent = status;
    const unf = $(".sh-meta .unfollow");
    unf.hidden = !cur;
    if (cur && unf.dataset.channel !== String(cur.channel_id)) {
      unf.dataset.channel = String(cur.channel_id);
      unf.dataset.name = cur.channel;
      if (unf.classList.contains("armed")) { clearTimeout(unf._timer); unf.classList.remove("armed"); unf.textContent = "Unfollow"; }
    }
    const upnext = queue.filter((i) => !cur || i.id !== cur.id);
    $(".mini .q .n").textContent = String(upnext.length);
    $(".mini .q").setAttribute("aria-label", `Up next: ${upnext.length}`);
    root.querySelectorAll('[data-p="toggle"], [data-p="restart"], [data-p="skip"], [data-p="done"]').forEach((b) => {
      b.disabled = !cur && b.dataset.p !== "skip";
    });
    renderPlayState();
    renderProgress();

    // Up next: everything but the current item, in queue order. Moves never swap with the current item.
    list.replaceChildren();
    const curIdx = cur ? indexOf(cur.id) : -1;
    upnext.forEach((item) => {
      const idx = indexOf(item.id);
      const li = el("li");
      const th = el("span", "th");
      if (item.thumb) {
        const im = el("img");
        im.src = item.thumb;
        im.alt = "";
        im.loading = "lazy";
        th.append(im);
      }
      const meta = el("span", "meta");
      const why = unplayable(item);
      meta.append(el("span", "t", item.title), el("span", "s", why ? `${item.channel} · ${why}` : item.channel));
      li.append(th, meta,
        iconBtn("up", "Move up", "up", item.id, idx === 0 || idx - 1 === curIdx),
        iconBtn("down", "Move down", "down", item.id, idx === queue.length - 1 || idx + 1 === curIdx),
        iconBtn("x", "Remove from queue", "remove", item.id));
      list.append(li);
    });
    $(".upnext .hint").hidden = upnext.length > 0;
  }

  function renderPlayState() {
    const isPlaying = !video.paused && !video.ended;
    root.querySelectorAll('[data-p="toggle"]').forEach((b) => {
      b.innerHTML = ICON(isPlaying ? "pause" : "play");
      b.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
    });
    root.classList.toggle("is-playing", isPlaying);
    if (ms) ms.playbackState = loadedId === null ? "none" : isPlaying ? "playing" : "paused";
  }
  function renderProgress() {
    const p = loadedId !== null && video.duration > 0 ? Math.min(100, (video.currentTime / video.duration) * 100) : 0;
    root.style.setProperty("--p", p.toFixed(2) + "%");
  }

  // ---- downloading: poll while something queued is still downloading ----
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = 0;
    if (queue.some(isDownloading)) pollTimer = setTimeout(() => { pollTimer = 0; refresh().catch(() => {}); }, POLL_MS);
  }

  // ---- expand / collapse ----
  const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  function move(parent) {
    if (video.parentElement === parent) return;
    const wasPlaying = !video.paused && !video.ended;
    parent.appendChild(video);
    if (wasPlaying && video.paused) video.play().catch(() => {});
  }
  // The open sheet owns a history entry (same URL, state.sheet) so Android Back closes it.
  function expand({ upNext = false, fromPop = false } = {}) {
    if (!queue.length) return;
    expectPop = false;
    if (!fromPop && !sheet.classList.contains("open") && !history.state?.sheet) {
      history.pushState({ ...(history.state || {}), sheet: true }, "");
    }
    move(stage);
    video.controls = true;
    sheet.removeAttribute("inert");
    sheet.classList.add("open");
    document.documentElement.classList.add("sheet-open");
    mini.setAttribute("inert", "");
    if (upNext) {
      const sec = $(".upnext");
      requestAnimationFrame(() => sec.scrollIntoView({ block: "start", behavior: reduceMotion() ? "auto" : "smooth" }));
    } else {
      $(".sheet-in").scrollTop = 0;
    }
    $('.sh-head [data-p="collapse"]').focus({ preventScroll: true });
  }
  function collapse({ fromPop = false } = {}) {
    if (!sheet.classList.contains("open")) return;
    sheet.classList.remove("open");
    sheet.setAttribute("inert", "");
    mini.removeAttribute("inert");
    document.documentElement.classList.remove("sheet-open");
    video.controls = false;
    move(hold);
    if (!root.hidden) $('.mini [data-p="expand"]').focus({ preventScroll: true });
    if (!fromPop && history.state?.sheet) {
      expectPop = true;
      history.back();
    }
  }
  // nav.js asks before handling Back/Forward: the sheet's own entries never load a page.
  document.addEventListener("nav:popstate", (e) => {
    const st = e.detail && e.detail.state;
    const open = sheet.classList.contains("open");
    if (expectPop) {
      expectPop = false;
      if (!st?.sheet) { e.preventDefault(); return; }
    }
    if (open && !st?.sheet) {
      e.preventDefault();
      collapse({ fromPop: true });
    } else if (!open && st?.sheet) {
      e.preventDefault();
      expand({ fromPop: true });
    }
  });

  // ---- actions ----
  function quietReload() {
    if (location.pathname !== "/" || !window.Nav) return;
    quietNav = true;
    window.Nav.reload();
  }
  function toggle() {
    if (!loadedId) return;
    if (video.paused || video.ended) video.play().catch(() => {});
    else video.pause();
  }
  function restart() {
    if (!loadedId) return;
    video.currentTime = 0;
  }
  // Drop an item locally right away (the next one starts without waiting for the network), then
  // tell the server. `gone` keeps responses already in flight from bringing it back.
  function dropLocal(id, autoplay) {
    gone.add(id);
    apply(queue.filter((i) => i.id !== id), autoplay);
  }
  function settle(id, run) {
    const clear = () => gone.delete(id);
    run.then(clear, () => { clear(); refresh().catch(() => {}); });
    return run;
  }
  function skip() {
    const cur = current();
    const id = cur ? cur.id : queue[0]?.id;
    if (id == null) return Promise.resolve(queue);
    if (cur) report();
    const wasPlaying = !video.paused && !video.ended;
    dropLocal(id, wasPlaying);
    return settle(id, op(() => retrying(() => api("DELETE", `/api/queue/${id}`))));
  }
  // `ended`: start the next ready item immediately from the local queue (no timers, no network,
  // so it works with the screen off), then mark watched and re-sync in the background.
  function advance() {
    const id = loadedId;
    if (id === null) return;
    dropLocal(id, true);
    renderPlayState();
    settle(id, op(async () => {
      await retrying(() => api("POST", `/api/videos/${id}/watched`, undefined, { keepalive: true }));
      return getQueue();
    })).then(quietReload, () => {});
  }
  function done({ autoplay, silent = false } = {}) {
    const cur = current();
    if (!cur) return Promise.resolve(queue);
    const id = cur.id;
    return op(async () => {
      await api("POST", `/api/videos/${id}/watched`);
      return getQueue();
    }, { autoplay }).then((q) => {
      quietReload();
      if (silent) return q;
      toast("Marked as watched", {
        label: "Undo",
        onAction: () => op(async () => {
          await api("DELETE", `/api/videos/${id}/watched`, { queue_index: 0 });
          return getQueue();
        }, { autoplay: false }).then(quietReload, () => toast("Couldn't undo")),
      });
      return q;
    });
  }

  function add(ids) {
    return op(() => api("POST", "/api/queue", { video_ids: ids }));
  }
  function playNext(id) {
    if (current()?.id === id) return Promise.resolve(queue);
    return op(() => api("POST", "/api/queue/next", { video_id: id }));
  }
  // ▶ Play: the old current moves to up next if paused/unstarted, leaves the queue if playing.
  function playNow(id, { autoplay = true } = {}) {
    if (current()?.id === id) {
      if (autoplay) video.play().catch(() => {});
      return Promise.resolve(queue);
    }
    skipped.delete(id);
    const keep = video.paused || video.ended;
    report(); // the old current keeps its position even if it leaves the queue
    return op(() => api("POST", "/api/queue/play", { video_id: id, keep_current: keep }), { autoplay });
  }
  function refresh({ resync = false } = {}) {
    return op(getQueue, { resync });
  }

  // ---- open-video (deep link /watch/{id}): load paused, open the sheet ----
  function openVideo(id) {
    return playNow(id, { autoplay: false }).then(() => {
      if (current()?.id !== id && !queue.some((i) => i.id === id)) {
        toast("This video is no longer available.");
      }
      expand();
    }, () => toast("Something went wrong"));
  }
  function watchPath() {
    const m = location.pathname.match(/^\/watch\/(\d+)\/?$/);
    return m ? Number(m[1]) : 0;
  }
  function leaveWatchPath() {
    history.replaceState(history.state, "", "/");
  }

  // ---- events ----
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-p]");
    if (!b || b.disabled) return;
    const id = Number(b.dataset.id);
    const fail = () => toast("Something went wrong");
    switch (b.dataset.p) {
      case "expand": expand(); break;
      case "queue": expand({ upNext: true }); break;
      case "collapse": collapse(); break;
      case "toggle": toggle(); break;
      case "restart": restart(); break;
      case "skip": skip().catch(fail); break;
      case "done": done().catch(fail); break;
      case "up": case "down":
        op(() => api("POST", `/api/queue/${id}/move`, { direction: b.dataset.p })).catch(fail); break;
      case "unfollow":
        if (window.TwoTap ? window.TwoTap.arm(b, "Unfollow?") : true) {
          unfollow(Number(b.dataset.channel), b.dataset.name || "channel").catch(fail);
        }
        break;
      case "remove":
        op(() => api("DELETE", `/api/queue/${id}`)).then(quietReload, fail); break;
    }
  });

  document.addEventListener("keydown", (e) => {
    // In fullscreen, Escape belongs to the browser (exit fullscreen), not to the sheet.
    if (e.key === "Escape" && sheet.classList.contains("open") && !document.fullscreenElement) {
      e.preventDefault();
      collapse();
    }
  });

  // Swipe down on the sheet header collapses it.
  let swipeY = null;
  const head = $(".sh-head");
  head.addEventListener("touchstart", (e) => { swipeY = e.touches[0].clientY; }, { passive: true });
  head.addEventListener("touchend", (e) => {
    if (swipeY !== null && e.changedTouches[0].clientY - swipeY > 60) collapse();
    swipeY = null;
  });
  head.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse") swipeY = e.clientY; });
  head.addEventListener("pointerup", (e) => {
    if (e.pointerType === "mouse" && swipeY !== null && e.clientY - swipeY > 60) collapse();
    if (e.pointerType === "mouse") swipeY = null;
  });

  video.addEventListener("loadedmetadata", () => { switching = false; positionState(); });
  video.addEventListener("play", () => {
    wantPlay = true;
    renderPlayState();
    positionState();
  });
  video.addEventListener("pause", () => {
    // At the end, `pause` comes right before `ended`: leave the play state to advance().
    if (video.ended) return;
    renderPlayState();
    positionState();
    if (switching) return; // caused by loading a new src, not by the user
    wantPlay = false;
    report();
  });
  video.addEventListener("timeupdate", () => {
    renderProgress();
    if (switching || loadedId === null) return;
    lastTime = video.currentTime;
    if (retry && retry.id === loadedId && lastTime > retry.at + 5) retry = null; // recovered
    // Media events keep firing in the background, timers are throttled: report from here.
    if (!video.paused && Date.now() - lastReport >= PROGRESS_MS) report();
  });
  video.addEventListener("durationchange", () => { renderProgress(); positionState(); });
  video.addEventListener("seeked", positionState);
  video.addEventListener("ratechange", positionState);
  video.addEventListener("ended", advance);
  video.addEventListener("error", () => {
    if (loadedId === null || !video.getAttribute("src")) return;
    const id = loadedId;
    const code = video.error && video.error.code;
    switching = false;
    if (code === 2 && !(retry && retry.id === id)) {
      // MEDIA_ERR_NETWORK: a blip. Reload the same file once and carry on where we were.
      retry = { id, at: lastTime };
      switching = true;
      setSrc(video.getAttribute("src"), id, lastTime);
      if (wantPlay) video.play().catch(() => {});
      return;
    }
    const resume = wantPlay;
    skipped.add(id);
    loadedId = null;
    toast("Couldn't play that one, skipping");
    if (code === 2) {
      apply(null, resume); // still failing after a retry: skip it here, keep it queued
    } else {
      // The file itself is broken: take it out of the queue here and on the server.
      dropLocal(id, resume);
      settle(id, op(() => api("DELETE", `/api/queue/${id}`))).then(quietReload, () => {});
    }
  });

  if (ms) {
    const handlers = {
      play: () => { if (loadedId !== null) video.play().catch(() => {}); },
      pause: () => video.pause(),
      previoustrack: () => restart(), // no history of played items: restart the current one
      nexttrack: () => { skip().catch(() => {}); },
      seekbackward: (d) => seekTo(video.currentTime - ((d && d.seekOffset) || SEEK_STEP)),
      seekforward: (d) => seekTo(video.currentTime + ((d && d.seekOffset) || SEEK_STEP)),
      seekto: (d) => {
        if (!d || !Number.isFinite(d.seekTime)) return;
        if (d.fastSeek && typeof video.fastSeek === "function") video.fastSeek(d.seekTime);
        else seekTo(d.seekTime);
        positionState();
      },
    };
    for (const [action, fn] of Object.entries(handlers)) {
      try { ms.setActionHandler(action, fn); } catch (e) { /* action not supported here */ }
    }
  }

  document.addEventListener("nav:loaded", () => {
    const quiet = quietNav;
    quietNav = false;
    const id = watchPath();
    if (id) {
      leaveWatchPath();
      openVideo(id);
      return;
    }
    if (!quiet) collapse();
    refresh().catch(() => {});
  });

  // Unfollow a channel: its other videos leave the queue; the one playing (if it's theirs) keeps playing.
  function unfollow(channelId, name) {
    const cur = current();
    const keep = cur && cur.channel_id === channelId ? cur.id : null;
    return op(() => api("POST", `/api/channels/${channelId}/unfollow`, { keep_video_id: keep })).then(() => {
      toast("Unfollowed " + (name || "channel"));
      quietReload();
    });
  }

  window.Player = {
    add, playNext, playNow, refresh, expand, collapse, skip, done, restart, unfollow,
    queueIndex: (id) => indexOf(id),
    get currentId() { return loadedId; },
    get queue() { return queue.map((i) => i.id); },
    get polling() { return pollTimer !== 0; },
  };

  // ---- first load ----
  const openId = Number(document.documentElement.dataset.openVideo) || 0;
  refresh().then(() => {
    if (openId) {
      if (watchPath()) leaveWatchPath();
      return openVideo(openId);
    }
  }).catch(() => {});
})();
