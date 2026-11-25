"use strict";

// ---------- helpers ----------
const api = async (method, url, body) => {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    keepalive: method !== "GET",
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
};

const setQueueCount = (n) => {
  const el = document.getElementById("queue-count");
  if (el) el.textContent = n;
};

let toastTimer;
const toast = (text, actionLabel, onAction) => {
  const t = document.getElementById("toast");
  t.replaceChildren();
  const span = document.createElement("span");
  span.textContent = text;
  t.append(span);
  if (actionLabel) {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = actionLabel;
    b.onclick = () => { t.hidden = true; clearTimeout(toastTimer); onAction(); };
    t.append(b);
  }
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
};

const markQueued = (btn) => {
  const s = document.createElement("span");
  s.className = "btn ghost";
  s.textContent = "Queued";
  btn.replaceWith(s);
};

const refreshEmpty = () => {
  const list = document.querySelector(".feed");
  const empty = document.querySelector(".empty");
  if (list && empty) {
    const any = list.querySelector(".item:not([hidden])");
    empty.hidden = !!any;
    const bulk = document.querySelector(".bulk");
    if (bulk) bulk.hidden = !any;
  }
};

// ---------- click actions ----------
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-action]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const action = btn.dataset.action;
  try {
    if (action === "queue") {
      btn.disabled = true;
      const q = await api("POST", "/api/queue", { video_ids: [id] });
      setQueueCount(q.length);
      markQueued(btn);
    } else if (action === "queue-all") {
      const ids = btn.dataset.ids.split(",").filter(Boolean).map(Number);
      const q = await api("POST", "/api/queue", { video_ids: ids });
      setQueueCount(q.length);
      document.querySelectorAll('[data-action="queue"]').forEach(markQueued);
      toast(`Added ${ids.length} to queue`);
    } else if (action === "watched") {
      const item = btn.closest(".item");
      await api("POST", `/api/videos/${id}/watched`);
      item.classList.add("leaving");
      setTimeout(() => { item.hidden = true; refreshEmpty(); }, 250);
      toast("Marked as watched", "Undo", async () => {
        await api("DELETE", `/api/videos/${id}/watched`);
        item.hidden = false;
        item.classList.remove("leaving");
        refreshEmpty();
      });
    } else if (action === "watched-back") {
      await api("POST", `/api/videos/${id}/watched`);
      location.href = "/";
    } else if (action === "retry") {
      await api("POST", `/api/videos/${id}/retry`);
      btn.textContent = "downloading…";
      btn.className = "btn ghost";
      btn.disabled = true;
    } else if (action === "up" || action === "down") {
      const q = await api("POST", `/api/queue/${id}/move`, { direction: action });
      Queue.render(q);
    } else if (action === "remove") {
      const q = await api("DELETE", `/api/queue/${id}`);
      Queue.render(q);
      if (Queue.currentId === id) Queue.playFirst(q);
    }
  } catch (e) {
    btn.disabled = false;
    toast(`Something went wrong: ${e.message}`);
  }
});

// Confirm-before-submit forms and auto-submitting selects/checkboxes.
document.addEventListener("submit", (ev) => {
  const msg = ev.target.dataset.confirm;
  if (msg && !confirm(msg)) ev.preventDefault();
});
document.addEventListener("change", (ev) => {
  if (ev.target.matches("[data-autosubmit]")) ev.target.form.submit();
});

// ---------- progress reporting ----------
const trackProgress = (video, getId, onEnded) => {
  let last = 0;
  const report = (position) => {
    const id = getId();
    if (!id || !video.duration || !isFinite(video.duration)) return Promise.resolve({ watched: false });
    return api("POST", `/api/videos/${id}/progress`, { position, duration: video.duration })
      .catch(() => ({ watched: false }));
  };
  video.addEventListener("timeupdate", () => {
    const now = Date.now();
    if (now - last > 10000) { last = now; report(video.currentTime); }
  });
  video.addEventListener("pause", () => { if (!video.ended) report(video.currentTime); });
  video.addEventListener("ended", async () => {
    await report(video.duration);
    if (onEnded) onEnded();
  });
  window.addEventListener("pagehide", () => { if (!video.ended) report(video.currentTime); });
};

const seekTo = (video, position) => {
  video.addEventListener("loadedmetadata", () => {
    if (position > 5 && position < video.duration * 0.95) video.currentTime = position;
  }, { once: true });
};

// ---------- single video page ----------
const single = document.getElementById("player");
if (single) {
  seekTo(single, Number(single.dataset.position || 0));
  trackProgress(single, () => single.dataset.videoId, null);
}

// ---------- queue page ----------
const Queue = {
  video: document.getElementById("qplayer"),
  list: document.getElementById("queue-list"),
  currentId: null,
  skipped: new Set(),  // videos that failed to play this session

  render(items) {
    setQueueCount(items.length);
    if (!this.list) return;
    this.list.replaceChildren(...items.map((v) => {
      const li = document.createElement("li");
      li.className = "qitem" + (v.id === this.currentId ? " playing" : "");
      li.dataset.id = v.id;
      const title = document.createElement("span");
      title.className = "qtitle";
      title.textContent = v.title;
      const small = document.createElement("small");
      small.textContent = v.channel + (v.src ? "" : " · downloading…");
      title.append(small);
      const btns = document.createElement("span");
      btns.className = "qbtns";
      for (const [action, label, aria] of [["up", "↑", "Move up"], ["down", "↓", "Move down"], ["remove", "✕", "Remove"]]) {
        const b = document.createElement("button");
        b.className = "btn icon";
        b.dataset.action = action;
        b.dataset.id = v.id;
        b.setAttribute("aria-label", aria);
        b.textContent = label;
        btns.append(b);
      }
      li.append(title, btns);
      return li;
    }));
    const empty = document.getElementById("queue-empty");
    if (empty) empty.hidden = items.length > 0;
  },

  playFirst(items) {
    const next = items.find((v) => v.src && !this.skipped.has(v.id));
    const wrap = document.getElementById("qplayer-wrap");
    const now = document.getElementById("qnow");
    if (!next) {
      this.currentId = null;
      this.video.removeAttribute("src");
      this.video.load();
      wrap.hidden = true;
      const waiting = items.some((v) => !v.src);
      now.textContent = waiting ? "Waiting for downloads…"
        : items.length ? "Couldn't play the rest of the queue." : "";
      this.render(items);
      return;
    }
    wrap.hidden = false;
    this.currentId = next.id;
    now.textContent = `${next.title} — ${next.channel}`;
    seekTo(this.video, next.position || 0);
    this.video.src = next.src;
    this.video.play().catch(() => {});
    this.render(items);
  },

  async init() {
    if (!this.video) return;
    trackProgress(this.video, () => this.currentId, async () => {
      const items = await api("GET", "/api/queue");
      this.playFirst(items.filter((v) => v.id !== this.currentId));
    });
    this.video.addEventListener("error", async () => {
      if (!this.currentId) return;
      this.skipped.add(this.currentId);
      toast("Couldn't play that one, skipping");
      this.playFirst(await api("GET", "/api/queue"));
    });
    this.playFirst(await api("GET", "/api/queue"));
  },
};
Queue.init();
