// Feed actions. Delegated on document: #main is swapped by nav.js.
(() => {
  const CHECK = '<svg class="i"><use href="#i-check"/></svg>';
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---- toast ----
  let toastTimer = 0;
  function toast(msg, { label, onAction, ms = 5000 } = {}) {
    const el = document.getElementById("toast");
    if (!el) return;
    clearTimeout(toastTimer);
    el.replaceChildren();
    const span = document.createElement("span");
    span.textContent = msg;
    el.append(span);
    el.style.paddingRight = label ? "" : "18px";
    if (label) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.addEventListener("click", () => {
        el.hidden = true;
        clearTimeout(toastTimer);
        if (onAction) onAction();
      });
      el.append(b);
    }
    el.setAttribute("role", "status");
    el.hidden = false;
    toastTimer = setTimeout(() => { el.hidden = true; }, ms);
  }
  window.Toast = { show: toast };

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(String(res.status));
    return res;
  }

  // ---- queue helpers (Player comes from player.js; fall back to the API) ----
  async function addToQueue(ids) {
    if (window.Player?.add) return window.Player.add(ids);
    await api("POST", "/api/queue", { video_ids: ids });
  }
  async function playNext(id) {
    if (window.Player?.playNext) return window.Player.playNext(id);
    await api("POST", "/api/queue/next", { video_id: id });
  }
  async function playNow(id) {
    if (window.Player?.playNow) return window.Player.playNow(id);
    window.Nav.go("/watch/" + id);
  }

  function markQueued(id) {
    $$(`[data-action="queue"][data-id="${id}"]`).forEach((el) => {
      if (el.classList.contains("add") || el.classList.contains("pill")) {
        const span = document.createElement("span");
        if (el.classList.contains("add")) {
          span.className = "add in-queue";
          span.setAttribute("role", "img");
          span.setAttribute("aria-label", "In queue");
          span.innerHTML = CHECK;
        } else {
          span.className = "pill in-queue";
          span.innerHTML = CHECK + "Queued";
        }
        el.replaceWith(span);
      } else {
        el.hidden = true; // menu entry
      }
    });
  }

  // ---- popover menu ----
  function closeMenus(except) {
    $$(".card .menu:not([hidden])").forEach((m) => {
      if (m === except) return;
      m.hidden = true;
      m.parentElement.querySelector('[data-action="menu"]')?.setAttribute("aria-expanded", "false");
    });
  }
  function toggleMenu(btn) {
    const menu = btn.closest(".card").querySelector(".menu");
    const open = menu.hidden;
    closeMenus(open ? menu : null);
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) menu.querySelector("button:not([hidden])")?.focus();
  }

  // ---- actions ----
  async function watched(id, el) {
    const idx = window.Player?.queueIndex ? window.Player.queueIndex(id) : -1;
    const box = el.closest(".card, .feature") || document.querySelector(`.card[data-id="${id}"], .feature[data-id="${id}"]`);
    try {
      await api("POST", `/api/videos/${id}/watched`);
    } catch (e) {
      toast("Couldn't mark as watched");
      return;
    }
    if (box) box.classList.add("leaving");
    toast("Marked as watched", {
      label: "Undo",
      onAction: async () => {
        try {
          await api("DELETE", `/api/videos/${id}/watched`, idx >= 0 ? { queue_index: idx } : undefined);
        } catch (e) {
          toast("Couldn't undo");
        }
        window.Nav.reload();
      },
    });
    setTimeout(() => window.Nav.reload(), 260);
  }

  async function run(action, id, el) {
    try {
      if (action === "play") {
        await playNow(id);
      } else if (action === "next") {
        await playNext(id);
        markQueued(id);
        toast("Playing next");
      } else if (action === "queue") {
        await addToQueue([id]);
        markQueued(id);
        toast("Added to queue");
      } else if (action === "queue-all") {
        const ids = (el.dataset.ids || "").split(",").filter(Boolean).map(Number);
        await addToQueue(ids);
        ids.forEach(markQueued);
        el.disabled = true;
        el.innerHTML = CHECK + "Queued";
        toast(`Added ${ids.length} to queue`);
      } else if (action === "retry") {
        await api("POST", `/api/videos/${id}/retry`);
        window.Nav.reload();
      } else if (action === "watched") {
        await watched(id, el);
      }
    } catch (e) {
      toast("Something went wrong");
    }
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    // Inline two-step confirm for forms with data-confirm (nav.js handles the submit itself).
    const sub = t.closest && t.closest("form[data-confirm] button[type=submit]");
    if (sub) {
      if (!sub.classList.contains("armed")) {
        e.preventDefault();
        const label = sub.textContent;
        sub.classList.add("armed");
        sub.textContent = "Confirm?";
        sub.title = sub.form.dataset.confirm;
        clearTimeout(sub._timer);
        sub._timer = setTimeout(() => {
          sub.classList.remove("armed");
          sub.textContent = label;
          sub.title = "";
        }, 4000);
      }
      return;
    }
    const el = t.closest && t.closest("[data-action]");
    if (!el || !el.closest("#main")) { closeMenus(); return; }
    const action = el.dataset.action;
    if (action === "menu") {
      e.preventDefault();
      toggleMenu(el);
      return;
    }
    if (el.disabled) return;
    closeMenus();
    run(action, Number(el.dataset.id), el);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const open = document.querySelector(".card .menu:not([hidden])");
      if (open) {
        open.parentElement.querySelector('[data-action="menu"]')?.focus();
        closeMenus();
      }
    } else if ((e.key === "Enter" || e.key === " ") && e.target.matches?.('.thumb[data-action="play"]')) {
      e.preventDefault();
      run("play", Number(e.target.dataset.id), e.target);
    }
  });

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.matches && el.matches("[data-autosubmit]") && el.form) el.form.requestSubmit();
  });

  document.addEventListener("nav:loaded", () => closeMenus());
})();
