// In-app navigation: swaps #main without reloading so the player persists.
(() => {
  const main = () => document.getElementById("main");
  const scrolls = new Map(); // history key -> scrollY
  let key = 0;
  let seq = 0;        // bumps on every load: only the newest response is applied
  let navPending = 0; // seq of an in-flight push/replace/pop navigation, 0 when none

  function stamp() {
    key = (history.state && history.state.key) || Date.now() + Math.random();
    history.replaceState({ key }, "");
  }

  function sameOriginLink(a) {
    if (!a || a.target || a.hasAttribute("download")) return false;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin) return false;
    return !u.pathname.startsWith("/media/") && !u.pathname.startsWith("/thumbs/");
  }

  // mode: "push" (new entry), "replace" (rewrite current entry), "pop" (history traversal), "reload".
  async function load(url, { method = "GET", body = null, mode = "push", restore = 0, form = null } = {}) {
    // A silent refresh never overrides a real navigation that is on its way.
    if (mode === "reload" && navPending) return;
    const my = ++seq;
    if (mode !== "reload") navPending = my;
    if (mode === "push") scrolls.set(key, window.scrollY);
    try {
      const res = await fetch(url, { method, body, redirect: "follow", headers: { "X-Requested-With": "nav" } });
      const type = res.headers.get("content-type") || "";
      if (!res.ok || !type.includes("text/html")) throw new Error("not a swappable page");
      const text = await res.text();
      if (my !== seq) return; // superseded by a newer load
      const doc = new DOMParser().parseFromString(text, "text/html");
      const next = doc.getElementById("main");
      if (!next) throw new Error("no main");
      main().replaceWith(next);
      document.title = doc.title;
      if (mode === "push" || mode === "replace") {
        const state = { key: Date.now() + Math.random() };
        // The open player sheet owns the top entry (same URL): a link replaces it, so Back
        // from the new page returns to the page itself rather than to a closed sheet.
        const replace = mode === "replace" || !!history.state?.sheet;
        history[replace ? "replaceState" : "pushState"](state, "", res.url);
        key = state.key;
      } else if (mode === "reload") {
        history.replaceState({ ...(history.state || {}), key }, "", res.url);
      }
      window.scrollTo(0, restore);
      document.dispatchEvent(new CustomEvent("nav:loaded"));
    } catch (e) {
      if (my !== seq) return;
      if (form) form.submit(); // a failed POST must not be retried as a GET
      else if (mode === "pop") location.reload();
      else location.assign(url);
    } finally {
      if (navPending === my) navPending = 0;
    }
  }

  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest("a[href]");
    if (!sameOriginLink(a)) return;
    e.preventDefault();
    load(a.href);
  });

  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (e.defaultPrevented || form.hasAttribute("data-native")) return;
    // data-confirm forms are confirmed inline by feed.js before they reach this point.
    e.preventDefault();
    const action = new URL(form.getAttribute("action") || location.href, location.href);
    const data = new URLSearchParams(new FormData(form, e.submitter));
    if ((form.method || "get").toLowerCase() === "post") {
      load(action.href, { method: "POST", body: data, form });
    } else {
      action.search = data.toString();
      load(action.href, { form });
    }
  });

  window.addEventListener("popstate", (e) => {
    // The player handles its own sheet entries (Back closes the expanded player).
    const ask = new CustomEvent("nav:popstate", { cancelable: true, detail: { state: e.state } });
    if (!document.dispatchEvent(ask)) return;
    scrolls.set(key, window.scrollY); // outgoing entry, under its own key
    const k = e.state && e.state.key;
    if (k) key = k;
    load(location.href, { mode: "pop", restore: scrolls.get(k) || 0 });
  });

  stamp();
  history.scrollRestoration = "manual";
  window.Nav = {
    go: (url, opts = {}) => load(url, { mode: opts.replace ? "replace" : "push" }),
    reload: () => load(location.href, { mode: "reload", restore: window.scrollY }),
  };
})();
