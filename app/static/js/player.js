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

  let queue = [];          // from /api/queue, in order
  let loadedId = null;     // id whose file is in <video>
  const skipped = new Set(); // ids that failed to play this session (v1 skip logic)
  let chain = Promise.resolve(); // serialises queue requests so responses apply in order
  let pollTimer = 0;
  let quietNav = false;    // our own Nav.reload() must not collapse the sheet
  let lastReport = 0;
  let wantPlay = false;    // the last load was asked to play (used when skipping a broken file)

  const toast = (msg, opts) => window.Toast?.show(msg, opts);

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }
  // Queue an API call; its JSON (the queue, unless `getQueue` says otherwise) becomes the new state.
  function op(fn, { autoplay } = {}) {
    const run = chain.then(async () => {
      const wasPlaying = !video.paused && !video.ended;
      const q = await fn();
      apply(q, autoplay === undefined ? wasPlaying : autoplay);
      return q;
    });
    chain = run.catch(() => {});
    return run;
  }
  const getQueue = () => api("GET", "/api/queue");

  const current = () => queue.find((i) => i.ready && i.src && !skipped.has(i.id)) || null;
  const indexOf = (id) => queue.findIndex((i) => i.id === id);

  // ---- state -> <video> ----
  function apply(q, autoplay) {
    if (Array.isArray(q)) queue = q;
    const cur = current();
    if (!cur) {
      if (loadedId !== null) unload();
    } else if (cur.id !== loadedId) {
      load(cur, autoplay);
    }
    render();
    schedulePoll();
  }

  function unload() {
    report();
    loadedId = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    if (navigator.mediaSession) navigator.mediaSession.metadata = null;
  }

  function load(item, autoplay) {
    if (loadedId !== null && !video.ended) report();
    loadedId = item.id;
    const start = item.position && item.duration && item.position < item.duration - 2 ? item.position : 0;
    video.src = item.src;
    if (start > 0) {
      const id = item.id;
      video.addEventListener("loadedmetadata", () => {
        if (loadedId === id && video.currentTime < 1) video.currentTime = start;
      }, { once: true });
    }
    wantPlay = !!autoplay;
    if (autoplay) video.play().catch(() => {});
    setMediaSession(item);
  }

  function setMediaSession(item) {
    const ms = navigator.mediaSession;
    if (!ms || typeof MediaMetadata === "undefined") return;
    ms.metadata = new MediaMetadata({
      title: item.title,
      artist: item.channel,
      artwork: item.thumb ? [{ src: new URL(item.thumb, location.href).href, sizes: "480x360", type: "image/jpeg" }] : [],
    });
  }

  // ---- progress ----
  function report(keepalive = false) {
    if (loadedId === null || !video.currentSrc || !(video.duration > 0)) return;
    lastReport = Date.now();
    fetch(`/api/videos/${loadedId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position: video.currentTime, duration: video.duration }),
      keepalive,
    }).catch(() => {});
  }
  setInterval(() => {
    if (!video.paused && Date.now() - lastReport >= PROGRESS_MS - 500) report();
  }, 1000);
  window.addEventListener("pagehide", () => report(true));

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

  function render() {
    root.hidden = queue.length === 0;
    if (!queue.length) {
      collapse();
      return;
    }
    const cur = current();
    const shown = cur || queue.find((i) => !skipped.has(i.id)) || queue[0];
    const status = cur ? shown.channel : shown.ready ? "Couldn't play" : "Downloading";
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
      meta.append(el("span", "t", item.title), el("span", "s", item.ready ? item.channel : `${item.channel} · Downloading`));
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
    if (navigator.mediaSession) navigator.mediaSession.playbackState = isPlaying ? "playing" : "paused";
  }
  function renderProgress() {
    const p = loadedId !== null && video.duration > 0 ? Math.min(100, (video.currentTime / video.duration) * 100) : 0;
    root.style.setProperty("--p", p.toFixed(2) + "%");
  }

  // ---- downloading: poll until something is playable ----
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (queue.length && !current()) pollTimer = setTimeout(() => refresh(), POLL_MS);
  }

  // ---- expand / collapse ----
  const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  function move(parent) {
    if (video.parentElement === parent) return;
    const wasPlaying = !video.paused && !video.ended;
    parent.appendChild(video);
    if (wasPlaying && video.paused) video.play().catch(() => {});
  }
  function expand({ upNext = false } = {}) {
    if (!queue.length) return;
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
  function collapse() {
    if (!sheet.classList.contains("open")) return;
    sheet.classList.remove("open");
    sheet.setAttribute("inert", "");
    mini.removeAttribute("inert");
    document.documentElement.classList.remove("sheet-open");
    video.controls = false;
    move(hold);
    if (!root.hidden) $('.mini [data-p="expand"]').focus({ preventScroll: true });
  }

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
  function skip() {
    const cur = current();
    const id = cur ? cur.id : queue[0]?.id;
    if (id == null) return Promise.resolve(queue);
    if (cur) report();
    return op(() => api("DELETE", `/api/queue/${id}`));
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
    return op(() => api("POST", "/api/queue/play", { video_id: id, keep_current: keep }), { autoplay });
  }
  function refresh() {
    return op(getQueue);
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
      case "remove":
        op(() => api("DELETE", `/api/queue/${id}`)).then(quietReload, fail); break;
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sheet.classList.contains("open")) {
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

  // iOS lets a media element play without a gesture only after play() was called inside one.
  let unlocked = false;
  document.addEventListener("click", (e) => {
    if (unlocked || !e.isTrusted) return;
    unlocked = true;
    if (!video.paused) return;
    const p = video.play();
    video.pause();
    p?.catch(() => {});
  }, true);

  video.addEventListener("play", renderPlayState);
  video.addEventListener("pause", () => {
    renderPlayState();
    if (!video.ended) report();
  });
  video.addEventListener("timeupdate", renderProgress);
  video.addEventListener("durationchange", renderProgress);
  video.addEventListener("ended", () => {
    renderPlayState();
    done({ autoplay: true, silent: true }).catch(() => refresh());
  });
  video.addEventListener("error", () => {
    if (loadedId === null || !video.getAttribute("src")) return;
    const resume = wantPlay || !video.paused;
    skipped.add(loadedId);
    loadedId = null;
    toast("Couldn't play that one, skipping");
    apply(null, resume);
  });

  const ms = navigator.mediaSession;
  if (ms) {
    try {
      ms.setActionHandler("play", () => video.play().catch(() => {}));
      ms.setActionHandler("pause", () => video.pause());
      ms.setActionHandler("nexttrack", () => skip().catch(() => {}));
    } catch (e) { /* unsupported action */ }
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

  window.Player = {
    add, playNext, playNow, refresh, expand, collapse, skip, done, restart,
    queueIndex: (id) => indexOf(id),
    get currentId() { return loadedId; },
    get queue() { return queue.map((i) => i.id); },
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
