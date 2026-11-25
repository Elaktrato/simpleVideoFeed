// In-app navigation: swaps #main without reloading so the player persists.
(() => {
  const main = () => document.getElementById("main");
  const scrolls = new Map(); // history key -> scrollY
  let key = 0;

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

  async function load(url, { method = "GET", body = null, push = true, replace = false, restore = null } = {}) {
    scrolls.set(key, window.scrollY);
    try {
      const res = await fetch(url, { method, body, redirect: "follow", headers: { "X-Requested-With": "nav" } });
      const type = res.headers.get("content-type") || "";
      if (!type.includes("text/html")) throw new Error("not html");
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const next = doc.getElementById("main");
      if (!next) throw new Error("no main");
      main().replaceWith(next);
      document.title = doc.title;
      if (push || replace) {
        const state = { key: Date.now() + Math.random() };
        history[replace ? "replaceState" : "pushState"](state, "", res.url);
        key = state.key;
      }
      window.scrollTo(0, restore == null ? 0 : restore);
      document.dispatchEvent(new CustomEvent("nav:loaded"));
    } catch (e) {
      location.assign(url);
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
    const msg = form.dataset.confirm;
    if (msg && !confirm(msg)) { e.preventDefault(); return; }
    e.preventDefault();
    const action = new URL(form.getAttribute("action") || location.href, location.href);
    if ((form.method || "get").toLowerCase() === "post") {
      load(action.href, { method: "POST", body: new URLSearchParams(new FormData(form)) });
    } else {
      action.search = new URLSearchParams(new FormData(form)).toString();
      load(action.href);
    }
  });

  window.addEventListener("popstate", (e) => {
    const k = e.state && e.state.key;
    if (k) key = k;
    load(location.href, { push: false, restore: scrolls.get(k) || 0 });
  });

  stamp();
  history.scrollRestoration = "manual";
  window.Nav = {
    go: (url, opts = {}) => load(url, { replace: !!opts.replace, push: !opts.replace }),
    reload: () => load(location.href, { push: false, restore: window.scrollY }),
  };
})();
