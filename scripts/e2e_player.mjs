// Browser check for the persistent player. Needs a server seeded with
// `python scripts/seed_demo.py <dir> --clips` (empty queue, ready a b c, downloading d,
// failed e, pending f). DATA_DIR must point at the same dir: one check flips d to "ready"
// in the SQLite DB mid-test, the way the downloader would.
//
//   DATA_DIR=<dir> NODE_PATH=$(npm root -g) node scripts/e2e_player.mjs http://127.0.0.1:8093 [shot-dir]
//
// Exits non-zero if any check fails.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
const shots = process.argv[3] || join(tmpdir(), "svf-e2e-shots");
mkdirSync(shots, { recursive: true });
const origin = new URL(base).origin;

const T = {
  a: "How I finally fixed my sourdough starter",
  b: "Building a tiny e-ink weather station",
  c: "Why your USB-C cable is lying to you",
  d: "Three quiet days on the Norwegian coast",
  e: "Restoring a 1970s mechanical keyboard",
  f: "A week of cooking with only a wok",
};

let failures = 0;
function ok(name, cond, detail = "") {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!cond) failures++;
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    ok(name, false, String(e.message || e).split("\n")[0]);
  }
}

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });

function watch(page, label) {
  page.on("pageerror", (e) => ok(`${label}: no page errors`, false, e.message));
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.protocol.startsWith("http") && u.origin !== origin) ok(`${label}: no third-party request`, false, r.url());
  });
}

const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
watch(page, "390");
await page.goto(base + "/");

const ids = {};
for (const [k, t] of Object.entries(T)) {
  ids[k] = Number(await page.locator(".card, .feature").filter({ hasText: t }).first().getAttribute("data-id"));
}
console.log("ids", ids);

const V = () => page.evaluate(() => {
  const v = document.getElementById("video");
  return { paused: v.paused, t: v.currentTime, ended: v.ended, src: v.currentSrc, n: document.querySelectorAll("video").length };
});
const queueIds = () => page.evaluate(async () => (await (await fetch("/api/queue")).json()).map((i) => i.id));
const playing = (id, timeout = 5000) =>
  page.waitForFunction(
    (id) => {
      const v = document.getElementById("video");
      return window.Player && window.Player.currentId === id && !v.paused && v.currentTime > 0;
    },
    id,
    { timeout },
  );
// The clips are 6 s long: keep the current one away from its end between checks.
const rewind = () => page.evaluate(() => {
  const v = document.getElementById("video");
  if (v.currentTime > 2) v.currentTime = 0.1;
});
const advancing = async () => {
  const t1 = (await V()).t;
  await page.waitForTimeout(600);
  const t2 = (await V()).t;
  return { t1, t2, ok: t2 > t1 };
};

await page.evaluate(() => {
  window.__video = document.getElementById("video");
  window.__maxVideos = 0;
  setInterval(() => {
    window.__maxVideos = Math.max(window.__maxVideos, document.querySelectorAll("video").length);
  }, 25);
});

// 1. + on a -> bar visible, video paused
await step("1. add a to empty queue", async () => {
  await page.locator(`.card[data-id="${ids.a}"] button.add`).click();
  await page.waitForFunction((id) => window.Player?.currentId === id, ids.a, { timeout: 5000 });
  const bar = page.locator("#player .mini");
  ok("1. bar visible after +", await bar.isVisible());
  await page.waitForTimeout(300);
  const v = await V();
  ok("1. video loaded paused", v.paused === true && v.src.endsWith(".mp4"), JSON.stringify(v));
  ok("1. bar shows a's title", (await bar.textContent()).includes(T.a));
});

// 2. Play on the poster (b) -> b plays, a moves to up next
await step("2. play b from the poster", async () => {
  await page.locator(`.feature[data-id="${ids.b}"] [data-action="play"]`).click();
  await playing(ids.b);
  ok("2. b is playing", true);
  const qa = await page.evaluate((id) => window.Player.queueIndex(id), ids.a);
  ok("2. queueIndex(a) === 1", qa === 1, `got ${qa}`);
  ok("2. server queue [b, a]", JSON.stringify(await queueIds()) === JSON.stringify([ids.b, ids.a]));
});

// 3. gear -> Channels -> back: playback continues, one <video> throughout
await step("3. navigate while playing", async () => {
  await rewind();
  await page.locator("a.gear").click();
  await page.waitForSelector(".chead h1");
  let adv = await advancing();
  ok("3. still advancing on /channels", adv.ok, `${adv.t1} -> ${adv.t2}`);
  await page.evaluate(() => history.back());
  await page.waitForSelector(".hello");
  adv = await advancing();
  ok("3. still advancing after back", adv.ok, `${adv.t1} -> ${adv.t2}`);
  const same = await page.evaluate(() => document.getElementById("video") === window.__video && window.__maxVideos);
  ok("3. same <video>, never more than one", same === 1, `max videos ${same}`);
});

// 4. bad channel URL -> error banner, still playing
await step("4. form submit while playing", async () => {
  await rewind();
  await page.locator("a.gear").click();
  await page.waitForSelector('form[action="/channels"] input[name=url]');
  await page.fill('form[action="/channels"] input[name=url]', "not a url");
  await page.locator('form[action="/channels"] button[type=submit]').click();
  await page.waitForSelector(".banner.error", { timeout: 10000 });
  const adv = await advancing();
  ok("4. error banner shown, still playing", adv.ok && !(await V()).paused, `${adv.t1} -> ${adv.t2}`);
  ok("4. same <video>", await page.evaluate(() => document.getElementById("video") === window.__video));
  await page.locator("a.back").click();
  await page.waitForSelector(".hello");
});

// 5. playNext(c) -> [b, c, a]
await step("5. playNext(c)", async () => {
  await rewind();
  await page.evaluate((id) => window.Player.playNext(id), ids.c);
  const q = await queueIds();
  ok("5. queue order [b, c, a]", JSON.stringify(q) === JSON.stringify([ids.b, ids.c, ids.a]), JSON.stringify(q));
  ok("5. Player queue matches", (await page.evaluate((id) => window.Player.queueIndex(id), ids.c)) === 1);
});

// 6. b ends -> c plays; b leaves the feed
await step("6. ended advances", async () => {
  await page.evaluate(() => {
    const v = document.getElementById("video");
    v.currentTime = v.duration - 0.3;
  });
  await playing(ids.c, 8000);
  ok("6. c plays after b ends", true);
  await page.evaluate(() => window.Nav.reload());
  await page.waitForSelector(".hello");
  const gone = await page.evaluate((id) => !document.querySelector(`#main [data-id="${id}"]`), ids.b);
  ok("6. b gone from the feed", gone);
  const q = await queueIds();
  ok("6. queue now [c, a]", JSON.stringify(q) === JSON.stringify([ids.c, ids.a]), JSON.stringify(q));
});

// 7. expand / Escape
await step("7. expand and collapse", async () => {
  await rewind();
  await page.locator("#player .mini .txt").click();
  const sheet = page.locator("#player .sheet");
  await sheet.waitFor({ state: "visible", timeout: 3000 });
  await page.waitForTimeout(400);
  const inSheet = await page.evaluate(() => !!document.getElementById("video").closest(".sheet"));
  const adv = await advancing();
  ok("7. sheet visible, video inside, still playing", inSheet && adv.ok, `${adv.t1} -> ${adv.t2}`);
  ok("7. up next lists a", (await page.locator("#player .upnext").textContent()).includes(T.a));
  await page.screenshot({ path: join(shots, "390-expanded.png") });
  await page.keyboard.press("Escape");
  await sheet.waitFor({ state: "hidden", timeout: 3000 });
  const adv2 = await advancing();
  ok("7. collapsed, still playing", adv2.ok && !(await page.evaluate(() => !!document.getElementById("video").closest(".sheet"))));
  ok("7. same <video>", await page.evaluate(() => document.getElementById("video") === window.__video));
});

// 8. screenshots, no horizontal scroll
const noHScroll = (p) => p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
await step("8. 390 screenshots", async () => {
  await page.evaluate(() => document.getElementById("video").pause());
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(shots, "390-feed.png") });
  ok("8. 390 feed: no horizontal scroll", await noHScroll(page));
  await page.locator("a.gear").click();
  await page.waitForSelector(".chead h1");
  await page.screenshot({ path: join(shots, "390-channels.png"), fullPage: true });
  ok("8. 390 channels: no horizontal scroll", await noHScroll(page));
});

await step("8. 1280 screenshots", async () => {
  const wide = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  watch(wide, "1280");
  await wide.goto(base + "/");
  await wide.waitForFunction(() => window.Player?.currentId > 0, null, { timeout: 5000 });
  await wide.waitForTimeout(300);
  ok("8. 1280: loads current paused", await wide.evaluate(() => document.getElementById("video").paused));
  await wide.screenshot({ path: join(shots, "1280-feed.png") });
  ok("8. 1280 feed: no horizontal scroll", await noHScroll(wide));
  await wide.locator("#player .mini .txt").click();
  await wide.locator("#player .sheet").waitFor({ state: "visible" });
  await wide.waitForTimeout(400);
  await wide.screenshot({ path: join(shots, "1280-expanded.png") });
  ok("8. 1280 expanded: no horizontal scroll", await noHScroll(wide));
  await wide.close();
});

await step("8. 360 no horizontal scroll", async () => {
  const narrow = await browser.newPage({ viewport: { width: 360, height: 740 } });
  await narrow.goto(base + "/");
  await narrow.waitForFunction(() => window.Player?.currentId > 0, null, { timeout: 5000 });
  ok("8. 360 feed: no horizontal scroll", await noHScroll(narrow));
  await narrow.screenshot({ path: join(shots, "360-feed.png") });
  await narrow.close();
});

// 9. deep link /watch/{id}: loads paused, sheet open; in-app navigation to /watch does the same
await step("9. /watch deep link", async () => {
  const p = await browser.newPage({ viewport: { width: 390, height: 844 } });
  watch(p, "watch");
  await p.goto(`${base}/watch/${ids.a}`);
  await p.locator("#player .sheet").waitFor({ state: "visible", timeout: 5000 });
  await p.waitForFunction((id) => window.Player?.currentId === id, ids.a, { timeout: 5000 });
  await p.waitForTimeout(300);
  ok("9. /watch: a current, paused, sheet open", await p.evaluate(() => document.getElementById("video").paused));
  ok("9. /watch: URL replaced with /", new URL(p.url()).pathname === "/");
  await p.keyboard.press("Escape");
  await p.locator("#player .sheet").waitFor({ state: "hidden", timeout: 3000 });
  await p.evaluate((id) => window.Nav.go(`/watch/${id}`), ids.c);
  await p.waitForFunction((id) => window.Player?.currentId === id, ids.c, { timeout: 5000 });
  await p.locator("#player .sheet").waitFor({ state: "visible", timeout: 5000 });
  ok("9. in-app /watch opens the video", true);
  await p.close();
});

// ---------- final fix wave: history, background playback, stale tabs, sticky current, errors ----------
await ctx.close();

const DATA_DIR = process.env.DATA_DIR;
ok("10+. DATA_DIR is set (needed to flip d to ready)", !!DATA_DIR);
function sql(stmt, ...params) {
  execFileSync("python3", [
    "-c",
    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1], timeout=10); c.execute(sys.argv[2], sys.argv[3:]); c.commit()",
    join(DATA_DIR, "feed.db"), stmt, ...params.map(String),
  ]);
}
async function api(method, path, body) {
  const r = await fetch(base + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}
async function setQueue(list) {
  for (const it of await api("GET", "/api/queue")) await api("DELETE", `/api/queue/${it.id}`);
  if (list.length) await api("POST", "/api/queue", { video_ids: list });
}
const serverQueue = async () => (await api("GET", "/api/queue")).map((i) => i.id);
for (const k of ["a", "b", "c"]) await api("DELETE", `/api/videos/${ids[k]}/watched`);
for (const k of ["a", "b", "c"]) await api("POST", `/api/videos/${ids[k]}/progress`, { position: 0, duration: 6 });

const bctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
await bctx.addInitScript(() => {
  // Capture Media Session handlers/positions and media "play" events (capture phase sees them).
  window.__ms = {};
  window.__pos = [];
  window.__plays = 0;
  const ms = navigator.mediaSession;
  if (ms) {
    const sah = ms.setActionHandler.bind(ms);
    ms.setActionHandler = (a, h) => { window.__ms[a] = h; return sah(a, h); };
    const sps = ms.setPositionState ? ms.setPositionState.bind(ms) : null;
    ms.setPositionState = (st) => { window.__pos.push(st); return sps && sps(st); };
  }
  document.addEventListener("play", () => { window.__plays++; }, true);
});
const bp = await bctx.newPage();
watch(bp, "bg");
const posts = []; // progress POSTs: {id, position}
bp.on("request", (r) => {
  const m = new URL(r.url()).pathname.match(/^\/api\/videos\/(\d+)\/progress$/);
  if (m && r.method() === "POST") posts.push({ id: Number(m[1]), position: JSON.parse(r.postData() || "{}").position, at: Date.now() });
});
const BV = () => bp.evaluate(() => {
  const v = document.getElementById("video");
  return { paused: v.paused, t: v.currentTime, src: v.currentSrc, id: window.Player.currentId };
});
const bPlaying = (id, timeout = 5000) =>
  bp.waitForFunction((id) => {
    const v = document.getElementById("video");
    return window.Player && window.Player.currentId === id && !v.paused && v.currentTime > 0;
  }, id, { timeout });
const bAdvancing = async () => {
  const t1 = (await BV()).t;
  await bp.waitForTimeout(600);
  const t2 = (await BV()).t;
  return { t1, t2, ok: t2 > t1 };
};
const bRewind = () => bp.evaluate(() => { document.getElementById("video").currentTime = 0.1; });
const setVisibility = (state) => bp.evaluate((state) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => state === "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
}, state);
// Each step below starts from a fresh load with a known queue, so one failure doesn't cascade.
async function fresh(list, { play = false } = {}) {
  for (const id of list) await api("DELETE", `/api/videos/${id}/watched`);
  await setQueue(list);
  for (const id of list) await api("POST", `/api/videos/${id}/progress`, { position: 0, duration: 6 });
  await bp.goto(base + "/");
  await bp.waitForSelector(".hello");
  await bp.waitForFunction(() => window.Player && document.readyState === "complete");
  await bp.evaluate(() => window.Player.refresh());
  if (play) {
    await bp.locator('#player .mini [data-p="toggle"]').click();
    await bp.waitForFunction(() => !document.getElementById("video").paused, null, { timeout: 5000 });
  }
}
const sheetOpen = () => bp.evaluate(() => document.querySelector("#player .sheet").classList.contains("open"));
const barStatus = () => bp.locator("#player .mini .s").textContent();
const fakeError = (code) => bp.evaluate((code) => {
  const v = document.getElementById("video");
  Object.defineProperty(v, "error", { configurable: true, get: () => ({ code }) });
  v.dispatchEvent(new Event("error"));
  delete v.error;
}, code);

await setQueue([ids.a, ids.c, ids.b]);
// Start on /channels so a Back that (wrongly) navigates stays inside the app.
await bp.goto(base + "/channels");
await bp.evaluate(() => window.Nav.go("/"));
await bp.waitForSelector(".hello");
await bp.waitForFunction((id) => window.Player?.currentId === id, ids.a, { timeout: 5000 });

// 10. No gesture-unlock hack: a first tap elsewhere must not start (and stop) the loaded video.
await step("10. first tap does not touch the video", async () => {
  await bp.waitForTimeout(300);
  await bp.locator(".hello").click();
  await bp.waitForTimeout(300);
  const r = await bp.evaluate(() => ({ plays: window.__plays, paused: document.getElementById("video").paused }));
  ok("10. no play() from a stray first tap", r.plays === 0 && r.paused, JSON.stringify(r));
});

// 11. Android Back with the sheet open collapses it instead of navigating.
await step("11. back closes the sheet", async () => {
  await bp.locator("#player .mini .txt").click();
  await bp.locator("#player .sheet").waitFor({ state: "visible", timeout: 3000 });
  await bp.locator('#player .sh-ctl [data-p="toggle"]').click();
  await bPlaying(ids.a);
  ok("11. sheet open: no pull-to-refresh", await bp.evaluate(() =>
    getComputedStyle(document.documentElement).overscrollBehaviorY === "none"));
  await bp.evaluate(() => { window.__main = document.getElementById("main"); });
  const url = bp.url();
  await bp.evaluate(() => history.back());
  await bp.waitForFunction(() => !document.querySelector("#player .sheet").classList.contains("open"), null, { timeout: 3000 });
  await bp.waitForTimeout(500);
  const r = await bp.evaluate(() => ({ same: document.getElementById("main") === window.__main, sheet: !!history.state?.sheet }));
  const adv = await bAdvancing();
  ok("11. back: sheet collapsed, page not reloaded, URL same, still playing",
    r.same && !r.sheet && bp.url() === url && adv.ok, JSON.stringify({ ...r, url: bp.url(), adv }));

  // ⌄ pops the sheet's history entry: a following navigation doesn't leave a stale entry behind.
  await bp.locator("#player .mini .txt").click();
  await bp.locator("#player .sheet").waitFor({ state: "visible", timeout: 3000 });
  ok("11. expand pushes a sheet entry", await bp.evaluate(() => history.state?.sheet === true));
  const len0 = (await bp.evaluate(() => history.length)) - 1; // length without the sheet entry
  await bp.locator('#player .sh-head [data-p="collapse"]').click();
  await bp.waitForFunction(() => !history.state?.sheet, null, { timeout: 3000 });
  await bp.waitForTimeout(200);
  ok("11. ⌄ collapse: page not reloaded", await bp.evaluate(() => document.getElementById("main") === window.__main));

  // Escape while fullscreen belongs to the fullscreen video, not the sheet.
  await bp.locator("#player .mini .txt").click();
  await bp.locator("#player .sheet").waitFor({ state: "visible", timeout: 3000 });
  await bp.evaluate(() => Object.defineProperty(document, "fullscreenElement", {
    configurable: true, get: () => document.getElementById("video"),
  }));
  await bp.keyboard.press("Escape");
  await bp.waitForTimeout(300);
  ok("12. Escape in fullscreen keeps the sheet open", await sheetOpen());
  await bp.evaluate(() => { delete document.fullscreenElement; });

  // Following a link while the sheet is open replaces the sheet entry.
  await bp.evaluate(() => window.Nav.go("/channels"));
  await bp.waitForSelector(".chead h1");
  ok("11. link from open sheet: collapsed, no extra entry",
    !(await sheetOpen()) && (await bp.evaluate(() => history.length)) === len0 + 1,
    `len ${await bp.evaluate(() => history.length)} vs ${len0 + 1}`);
  await bp.evaluate(() => history.back());
  await bp.waitForSelector(".hello");
  ok("11. back from there returns to the feed, sheet closed", !(await sheetOpen()) && new URL(bp.url()).pathname === "/");
  ok("11. still playing a", (await bAdvancing()).ok && (await BV()).id === ids.a);
});

// 13. Background: hidden page keeps playing, auto-advances, Media Session is complete.
await step("13. background playback", async () => {
  await fresh([ids.a, ids.c, ids.b], { play: true });
  await bPlaying(ids.a);
  await bp.waitForTimeout(700);
  const before = posts.length;
  await setVisibility("hidden");
  await bp.waitForTimeout(400);
  ok("13. hidden: reported progress", posts.slice(before).some((p) => p.id === ids.a), JSON.stringify(posts.slice(before)));
  const adv = await bAdvancing();
  ok("13. hidden: still playing", adv.ok, JSON.stringify(adv));
  await bp.evaluate(() => {
    const v = document.getElementById("video");
    v.addEventListener("ended", () => {
      window.__afterEnded = { id: window.Player.currentId, paused: v.paused, ps: navigator.mediaSession.playbackState };
    }, { once: true });
    v.currentTime = v.duration - 0.3;
  });
  await bPlaying(ids.c, 8000);
  const ae = await bp.evaluate(() => window.__afterEnded);
  ok("13. ended switches to the next item synchronously (no gap)",
    ae && ae.id === ids.c && !ae.paused && ae.ps === "playing", JSON.stringify(ae));
  ok("13. hidden: c plays after a ends", (await bp.evaluate(() => document.hidden)) === true);
  await bp.waitForFunction((want) => fetch("/api/queue").then((r) => r.json())
    .then((q) => JSON.stringify(q.map((i) => i.id)) === want), JSON.stringify([ids.c, ids.b]), { timeout: 5000 });
  ok("13. server synced: queue [c, b]", true);
  const md = await bp.evaluate(() => {
    const m = navigator.mediaSession.metadata;
    return m && { title: m.title, artist: m.artist, art: m.artwork.map((a) => ({ type: a.type, sizes: a.sizes, src: a.src })) };
  });
  ok("13. media metadata is c", md && md.title === T.c && md.artist === "Circuit Notes", JSON.stringify(md));
  ok("13. artwork: jpeg, no hard-coded size", md && md.art.length === 1 && md.art[0].type === "image/jpeg" &&
    (!md.art[0].sizes || md.art[0].sizes === "any") && md.art[0].src.startsWith(origin), JSON.stringify(md?.art));
  const hs = await bp.evaluate(() => Object.keys(window.__ms).filter((k) => typeof window.__ms[k] === "function").sort());
  const want = ["nexttrack", "pause", "play", "previoustrack", "seekbackward", "seekforward", "seekto"];
  ok("13. media session handlers", want.every((h) => hs.includes(h)), hs.join(","));
  const pos = await bp.evaluate(() => window.__pos.slice(-1)[0]);
  ok("13. setPositionState with finite duration", pos && Number.isFinite(pos.duration) && pos.duration > 0 &&
    pos.position >= 0 && pos.position <= pos.duration, JSON.stringify(pos));

  await bp.evaluate(() => { document.getElementById("video").currentTime = 1; });
  await bp.evaluate(() => window.__ms.seekforward({ action: "seekforward", seekOffset: 2 }));
  let t = (await BV()).t;
  ok("13. seekforward honours seekOffset", t >= 2.9 && t < 3.6, String(t));
  await bp.evaluate(() => window.__ms.seekbackward({ action: "seekbackward" }));
  t = (await BV()).t;
  ok("13. seekbackward defaults to 10 s (clamped at 0)", t < 0.6, String(t));
  await bp.evaluate(() => window.__ms.seekto({ action: "seekto", seekTime: 4 }));
  t = (await BV()).t;
  ok("13. seekto", Math.abs(t - 4) < 0.6, String(t));
  await bp.evaluate(() => window.__ms.previoustrack({ action: "previoustrack" }));
  t = (await BV()).t;
  ok("13. previoustrack restarts", t < 0.6, String(t));
  await bp.evaluate(() => window.__ms.nexttrack({ action: "nexttrack" }));
  await bPlaying(ids.b, 5000);
  ok("13. nexttrack skips to b, playing", true);
  ok("13. server queue [b]", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b]));
  await bp.evaluate(() => window.__ms.pause({ action: "pause" }));
  ok("13. pause handler pauses", (await BV()).paused);
  await setVisibility("visible");
});

// 14. Stale tab: becoming visible re-syncs position; no progress posted for a dequeued item.
await step("14. stale tab", async () => {
  await fresh([ids.c, ids.b]);
  await bp.waitForFunction((id) => window.Player.currentId === id && document.getElementById("video").readyState >= 1, ids.c, { timeout: 5000 });
  await bp.waitForTimeout(300);
  await setVisibility("hidden");
  await api("POST", `/api/videos/${ids.c}/progress`, { position: 3.5, duration: 6 }); // "the phone" moved on
  await setVisibility("visible");
  await bp.waitForFunction(() => Math.abs(document.getElementById("video").currentTime - 3.5) < 0.5, null, { timeout: 3000 });
  ok("14. visible: paused video seeks to the server position", true);

  await api("DELETE", `/api/queue/${ids.c}`); // finished/removed elsewhere
  await api("POST", `/api/videos/${ids.c}/progress`, { position: 2, duration: 6 });
  const before = posts.length;
  await setVisibility("hidden");
  await setVisibility("visible");
  await bp.waitForFunction((id) => window.Player.currentId === id, ids.b, { timeout: 3000 });
  await bp.waitForTimeout(300);
  const stale = posts.slice(before).filter((p) => p.id === ids.c);
  ok("14. no progress posted for the dequeued item", stale.length === 0, JSON.stringify(stale));
});

// 15. Sticky current: a downloading item ahead of the playing one finishing must not yank playback.
await step("15. sticky current", async () => {
  await fresh([ids.d, ids.b]);
  await bp.waitForFunction((id) => window.Player.currentId === id, ids.b, { timeout: 5000 });
  await bp.locator('#player .mini [data-p="toggle"]').click();
  await bPlaying(ids.b);
  await bp.waitForFunction((want) => fetch("/api/queue").then((r) => r.json())
    .then((q) => JSON.stringify(q.map((i) => i.id)) === want), JSON.stringify([ids.b, ids.d]), { timeout: 3000 })
    .catch(() => {});
  ok("15. loaded item pinned to the head: [b, d]", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b, ids.d]),
    JSON.stringify(await serverQueue()));
  sql("UPDATE videos SET status = 'ready', file_path = ? WHERE id = ?", join(DATA_DIR, "videos", "demoa000000.mp4"), ids.d);
  await bRewind();
  await bp.evaluate(() => window.Nav.go("/channels"));
  await bp.waitForSelector(".chead h1");
  await bp.waitForTimeout(800);
  const v = await BV();
  ok("15. b keeps playing after d became ready", v.id === ids.b && !v.paused, JSON.stringify(v));
  await bp.evaluate(() => window.Nav.go("/"));
  await bp.waitForSelector(".hello");
});

// 16. Bar labels by status; poll only while something is downloading.
await step("16. status labels", async () => {
  await fresh([]);
  await setQueue([ids.e]);
  await bp.evaluate(() => window.Player.refresh());
  await bp.waitForFunction(() => window.Player.currentId === null, null, { timeout: 3000 });
  let s = await barStatus();
  ok("16. failed item: 'Download failed', no polling", s === "Download failed" && !(await bp.evaluate(() => window.Player.polling)), s);
  await setQueue([ids.f]);
  await bp.evaluate(() => window.Player.refresh());
  s = await barStatus();
  ok("16. pending item: 'Downloading', polling", s === "Downloading" && (await bp.evaluate(() => window.Player.polling)), s);
  await setQueue([ids.b, ids.e, ids.f]);
  await bp.evaluate(() => window.Player.refresh());
  const up = await bp.locator("#player .upnext ol").textContent();
  ok("16. up next labels", up.includes("Download failed") && up.includes("Downloading"), up);
  ok("16. polling while f is still downloading", await bp.evaluate(() => window.Player.polling));
  await setQueue([ids.b, ids.e]);
  await bp.evaluate(() => window.Player.refresh());
  ok("16. nothing downloading: no polling", !(await bp.evaluate(() => window.Player.polling)));
});

// 17. Playback errors: network blip retries in place; user pause is respected; others leave the queue.
await step("17. playback errors", async () => {
  await fresh([ids.c]);
  await bp.evaluate((id) => window.Player.playNow(id), ids.b);
  await bPlaying(ids.b);
  ok("17. queue [b, c]", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b, ids.c]));
  await bp.evaluate(() => { document.getElementById("video").currentTime = 2; });
  await bp.waitForTimeout(300);
  await fakeError(2);
  await bp.waitForTimeout(1500);
  let v = await BV();
  ok("17. network error: same item reloaded, resumed near saved time", v.id === ids.b && !v.paused && v.t >= 1.9, JSON.stringify(v));
  ok("17. network error: still queued", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b, ids.c]));
});

await step("17b. decode error after a user pause", async () => {
  await fresh([ids.c]);
  await bp.evaluate((id) => window.Player.playNow(id), ids.b); // loads with autoplay
  await bPlaying(ids.b);
  await bp.locator('#player .mini [data-p="toggle"]').click(); // user pauses
  await bp.waitForTimeout(200);
  await fakeError(4);
  await bp.waitForFunction((id) => window.Player.currentId === id, ids.c, { timeout: 3000 });
  ok("17. decode error: local queue drops b at once", !(await bp.evaluate((id) => window.Player.queue.includes(id), ids.b)));
  await bp.waitForTimeout(500);
  const v = await BV();
  ok("17. user had paused: next item stays paused", v.paused, JSON.stringify(v));
  ok("17. decode error: server queue [c]", JSON.stringify(await serverQueue()) === JSON.stringify([ids.c]),
    JSON.stringify(await serverQueue()));
});

// 18. Navigation: a slow older response must not replace a newer page.
await step("18. nav drops stale responses", async () => {
  await fresh([]);
  await bp.route("**/channels", async (r) => { await new Promise((res) => setTimeout(res, 1500)); await r.continue(); });
  await bp.evaluate(() => { window.Nav.go("/channels"); setTimeout(() => window.Nav.go("/"), 50); });
  await bp.waitForTimeout(2500);
  const r = await bp.evaluate(() => ({ path: location.pathname, feed: !!document.querySelector(".hello"), ch: !!document.querySelector(".chead") }));
  ok("18. newest navigation wins", r.path === "/" && r.feed && !r.ch, JSON.stringify(r));
  await bp.unroute("**/channels");
});

// 19. Card ⋯ → Unfollow channel needs a second tap.
await step("19. card unfollow asks to confirm", async () => {
  await fresh([ids.b, ids.c]);
  await bp.evaluate(() => window.Nav.go("/?tab=week"));
  await bp.waitForSelector(".grid2");
  const card = bp.locator(`.card:has([data-action="menu"][data-id="${ids.c}"])`).first();
  await card.locator('[data-action="menu"]').click();
  const btn = card.locator('[data-action="unfollow"]');
  await btn.click();
  ok("19. first tap shows Unfollow?", (await btn.textContent()).includes("Unfollow?"), await btn.textContent());
  ok("19. first tap changes nothing", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b, ids.c]),
    JSON.stringify(await serverQueue()));
  await bp.keyboard.press("Escape");
});

// 20. Unfollow from the player: the playing video keeps playing, the channel's other items leave.
await step("20. unfollow from the sheet", async () => {
  await fresh([ids.b, ids.c], { play: true });
  await bPlaying(ids.b);
  await bp.evaluate(() => window.Player.expand());
  const btn = bp.locator('#player [data-p="unfollow"]');
  ok("20. sheet shows Unfollow", await btn.isVisible());
  await btn.click();
  ok("20. first tap only arms", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b, ids.c]));
  await btn.click();
  await bp.waitForFunction((c) => !window.Player.queue.includes(c), ids.c, { timeout: 3000 });
  ok("20. queue is just b", JSON.stringify(await serverQueue()) === JSON.stringify([ids.b]), JSON.stringify(await serverQueue()));
  ok("20. b still playing, same element", (await bAdvancing()).ok && (await bp.evaluate(() => document.querySelectorAll("video").length)) === 1);
  const t = await bp.locator("#toast").textContent();
  ok("20. toast says Unfollowed", /^\s*Unfollowed Circuit Notes/.test(t), t);
  await bp.evaluate(() => { const v = document.getElementById("video"); v.currentTime = v.duration - 0.3; });
  await bp.waitForFunction(() => window.Player.queue.length === 0, null, { timeout: 8000 });
  ok("20. nothing from that channel plays after b", (await bp.evaluate(() => window.Player.currentId)) === null);
  await bp.evaluate(() => window.Player.collapse());
  await bp.evaluate(() => window.Nav.go("/?tab=week"));
  await bp.waitForSelector(".hello");
  const html = await bp.locator("#main").innerHTML();
  ok("20. feed no longer shows the channel", !html.includes("Circuit Notes"));
});

await bctx.close();

await browser.close();
console.log(`\nscreenshots in ${shots}`);
console.log(failures ? `${failures} check(s) FAILED` : "all checks passed");
process.exit(failures ? 1 : 0);
