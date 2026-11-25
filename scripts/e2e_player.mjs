// Browser check for the persistent player. Needs a server seeded with
// `python scripts/seed_demo.py <dir> --clips` (empty queue, ready a b c, downloading d).
//
//   NODE_PATH=$(npm root -g) node scripts/e2e_player.mjs http://127.0.0.1:8093 [shot-dir]
//
// Exits non-zero if any check fails.
import { createRequire } from "node:module";
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

await browser.close();
console.log(`\nscreenshots in ${shots}`);
console.log(failures ? `${failures} check(s) FAILED` : "all checks passed");
process.exit(failures ? 1 : 0);
